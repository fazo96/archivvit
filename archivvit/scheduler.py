"""Runs a scrape pass every `scrape_interval_minutes`, or on demand, in a background thread."""

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config, scrape
from .client import Blocked, Reddit
from .store import Store

log = logging.getLogger(__name__)


class Scheduler(threading.Thread):
    def __init__(self, data_dir: Path):
        super().__init__(name="scheduler", daemon=True)
        self.data_dir = data_dir
        self.wake = threading.Event()
        self.running_since: datetime | None = None
        self.last_run: dict | None = None
        self.next_run: datetime | None = None

    def trigger(self):
        """Run a pass now (or right after the one in progress)."""
        self.next_run = datetime.now(timezone.utc)
        self.wake.set()

    def reschedule(self):
        """Settings changed: recompute the next run from the last one."""
        self.next_run = None
        self.wake.set()

    def _plan(self, cfg):
        if self.next_run is not None:
            return
        interval = timedelta(minutes=cfg.scrape_interval_minutes)
        if self.last_run:
            last = self.last_run["started"]
        else:
            store = Store(cfg.db_path)
            try:
                at = store.last_hot_at()
            finally:
                store.close()
            last = datetime.fromisoformat(at) if at else None
        now = datetime.now(timezone.utc)
        # After a restart, carry on from the last pass instead of scraping straight away.
        self.next_run = max(now + timedelta(seconds=10), last + interval) if last else now + timedelta(seconds=10)

    def run(self):
        while True:
            cfg = config.load(self.data_dir)
            self._plan(cfg)
            wait = (self.next_run - datetime.now(timezone.utc)).total_seconds()
            if wait > 0:
                self.wake.wait(timeout=wait)
                self.wake.clear()
                continue
            planned = self.next_run
            self._pass(cfg)
            if self.next_run == planned:  # not re-triggered or rescheduled meanwhile
                self.next_run = None

    def _pass(self, cfg):
        started = datetime.now(timezone.utc)
        self.running_since = started
        t = time.monotonic()
        store = Store(cfg.db_path)
        result = {"started": started, "ok": False}
        try:
            result.update(scrape.scrape(cfg, store, Reddit(cfg, store)))
            result["ok"] = not result["errors"]
        except Blocked as e:
            log.error("blocked: %s", e)
            result["errors"] = [f"blocked: {e}"]
        except Exception as e:
            log.exception("scrape pass failed")
            result["errors"] = [f"{type(e).__name__}: {e}"]
        finally:
            store.close()
            result["seconds"] = round(time.monotonic() - t)
            self.last_run = result
            self.running_since = None
        log.info("pass done in %ds: %s", result["seconds"], {k: v for k, v in result.items() if k != "started"})
