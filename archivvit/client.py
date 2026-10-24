"""HTTP client for Reddit: Chrome TLS fingerprint + the browser's cookies, politely paced."""

import logging
import random
import time

from curl_cffi import requests as creq

from . import session
from .config import Config
from .parse import BASE, is_challenge
from .store import Store

log = logging.getLogger(__name__)


class Blocked(Exception):
    pass


class Reddit:
    def __init__(self, cfg: Config, store: Store):
        self.cfg = cfg
        self.store = store
        self.http = None
        self.next_at = 0.0
        self.remaining = None
        self.reset_at = None
        self.requests = 0

    def _connect(self, fresh: bool):
        cookies = None if fresh else session.load(self.cfg.cookies_path)
        if not cookies:
            warmup = f"{BASE}/r/{self.cfg.subreddits[0]}/hot/"
            cookies = session.browser_cookies(self.cfg.user_agent, warmup)
            session.save(self.cfg.cookies_path, cookies)
        self.http = creq.Session(impersonate="chrome")
        for c in cookies:
            self.http.cookies.set(c["name"], c["value"], domain=c["domain"], path=c["path"])

    def _csrf(self) -> str:
        return next((c.value for c in self.http.cookies.jar if c.name == "csrf_token"), "")

    def _pace(self):
        if self.remaining is not None and self.remaining < self.cfg.ratelimit_floor and self.reset_at:
            wait = self.reset_at - time.monotonic()
            if wait > 0:
                log.info("rate limit: %d left, waiting %.0fs for the window to reset", self.remaining, wait)
                time.sleep(wait + 1)
            self.remaining = None
        wait = self.next_at - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self.next_at = time.monotonic() + random.uniform(self.cfg.min_delay, self.cfg.max_delay)

    def request(self, method: str, url: str, data: dict | None = None) -> str:
        if self.http is None:
            self._connect(fresh=False)
        for attempt in range(3):
            self._pace()
            if method == "POST":
                data = {**(data or {}), "csrf_token": self._csrf()}
            t = time.monotonic()
            r = self.http.request(method, url, data=data, timeout=30)
            self.requests += 1
            ms = int((time.monotonic() - t) * 1000)
            rem = r.headers.get("x-ratelimit-remaining")
            reset = r.headers.get("x-ratelimit-reset")
            if rem is not None:
                self.remaining = float(rem)
            if reset is not None:
                self.reset_at = time.monotonic() + float(reset)
            challenge = r.status_code == 200 and is_challenge(r.text)
            note = "challenge" if challenge else None
            self.store.log_fetch(method, url, r.status_code, len(r.content), ms, self.remaining, note)
            log.debug("%s %s -> %d %dB %dms rl=%s", method, url, r.status_code, len(r.content), ms, rem)

            if r.status_code == 429:
                wait = float(reset or 60) + 1
                log.warning("429 on %s, sleeping %.0fs", url, wait)
                time.sleep(wait)
                continue
            if r.status_code == 403 or challenge:
                if attempt == 0:
                    log.warning("%s on %s, refreshing browser cookies", note or 403, url)
                    self._connect(fresh=True)
                    continue
                raise Blocked(f"{method} {url}: {note or r.status_code} after a fresh browser session")
            if r.status_code >= 400:
                raise RuntimeError(f"{method} {url}: HTTP {r.status_code}")
            return r.text
        raise Blocked(f"{method} {url}: still rate limited")

    def get(self, url: str) -> str:
        return self.request("GET", url)

    def post(self, url: str, data: dict) -> str:
        return self.request("POST", url, data)
