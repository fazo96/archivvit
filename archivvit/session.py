"""Pass Reddit's JS bot check once in headless Chromium and keep the cookies it sets."""

import json
import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)

# Cookies the check hands out; their presence means it passed.
REQUIRED = {"csrf_token", "loid"}


def browser_cookies(user_agent: str, warmup_url: str) -> list[dict]:
    from playwright.sync_api import sync_playwright

    t = time.monotonic()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            ctx = browser.new_context(user_agent=user_agent, locale="en-US", viewport={"width": 1280, "height": 900})
            page = ctx.new_page()
            page.goto(warmup_url, wait_until="domcontentloaded")
            for _ in range(30):
                cookies = ctx.cookies()
                if REQUIRED <= {c["name"] for c in cookies}:
                    break
                page.wait_for_timeout(500)
            else:
                raise RuntimeError(f"bot check did not pass, got cookies {sorted(c['name'] for c in cookies)}")
        finally:
            browser.close()
    log.info("got browser cookies in %.1fs", time.monotonic() - t)
    return cookies


def load(path: Path) -> list[dict] | None:
    try:
        return json.loads(path.read_text())["cookies"]
    except (FileNotFoundError, KeyError, ValueError):
        return None


def save(path: Path, cookies: list[dict]):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"saved_at": time.time(), "cookies": cookies}, indent=1))
    tmp.replace(path)
