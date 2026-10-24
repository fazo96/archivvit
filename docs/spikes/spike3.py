import json, sys, time, pathlib, re, html
from collections import Counter
from playwright.sync_api import sync_playwright

OUT = pathlib.Path(sys.argv[1])
SUB = sys.argv[2] if len(sys.argv) > 2 else "NixOS"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
posts = json.loads((OUT.parent / "out2" / "posts.json").read_text())


def log(*a):
    print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)


def expand(page, max_rounds=40):
    """Click every load-more control (nested replies + top-level pagination) until none remain."""
    clicks = 0
    for _ in range(max_rounds):
        btns = page.locator(
            "faceplate-partial.more-comments-partial:not([loaded]) button, "
            "shreddit-comment-tree faceplate-partial[loading=action] button, "
            "button:has-text('View more comments'), button:has-text('more repl')"
        )
        n = btns.count()
        if n == 0:
            break
        try:
            btns.first.scroll_into_view_if_needed(timeout=3000)
            btns.first.click(timeout=3000)
            clicks += 1
        except Exception:
            page.mouse.wheel(0, 4000)
        page.wait_for_timeout(700)
    return clicks


rl = []
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(user_agent=UA, locale="en-US", viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    page.on("response", lambda r: rl.append((time.time(), r.status, r.url[:90], r.headers.get("x-ratelimit-remaining"), r.headers.get("x-ratelimit-reset"))) if r.headers.get("x-ratelimit-remaining") else None)

    threads = sorted(posts, key=lambda x: -int(x.get("comment-count") or 0))[:5]
    for i, tp in enumerate(threads):
        url = "https://www.reddit.com" + tp["permalink"]
        t = time.time()
        page.goto(url, wait_until="domcontentloaded")
        try:
            page.wait_for_selector("shreddit-comment", timeout=15000)
        except Exception:
            log("no comments rendered for", url)
            continue
        before = page.locator("shreddit-comment").count()
        clicks = expand(page) if i < 2 else 0
        comments = page.eval_on_selector_all("shreddit-comment", "els => els.map(e => [e.getAttribute('thingid'), e.getAttribute('parentid'), e.getAttribute('depth'), !!e.querySelector('[slot=comment]')])")
        ids = [c[0] for c in comments]
        log(f"{tp['comment-count']:>4} expected | {before:>3} initial -> {len(comments):>3} after {clicks} clicks | unique={len(set(ids))} "
            f"with-body={sum(c[3] for c in comments)} maxdepth={max(int(c[2]) for c in comments)} | {time.time()-t:.1f}s | {tp['post-title'][:40]}")
        if i == 0:
            (OUT / "thread_expanded.html").write_text(page.content())
        page.wait_for_timeout(1500)

    cookies = ctx.cookies()
    browser.close()

log("ratelimit trail (remaining, reset):", [(r[3], r[4]) for r in rl[::max(1, len(rl)//10)]] + [(rl[-1][3], rl[-1][4])] if rl else None)
log("total rate-limited responses:", len(rl))

# curl_cffi with Chrome TLS fingerprint + browser cookies
from curl_cffi import requests as creq
s = creq.Session(impersonate="chrome")
for c in cookies:
    s.cookies.set(c["name"], c["value"], domain=c["domain"], path=c["path"])
for name, url in [
    ("hot html", f"https://www.reddit.com/r/{SUB}/hot/"),
    ("thread html", "https://www.reddit.com" + threads[0]["permalink"]),
    ("hot .rss", f"https://www.reddit.com/r/{SUB}/hot/.rss"),
]:
    time.sleep(2)
    r = s.get(url, timeout=30)
    log(f"curl_cffi+cookies {name}: {r.status_code} {len(r.text)}B posts={r.text.count('<shreddit-post')} comments={r.text.count('<shreddit-comment ')} "
        f"challenge={'js_challenge' in r.text} rl={r.headers.get('x-ratelimit-remaining')}")

time.sleep(2)
r = creq.get("https://www.reddit.com" + threads[0]["permalink"], impersonate="chrome", timeout=30)
log(f"curl_cffi no cookies thread: {r.status_code} {len(r.text)}B comments={r.text.count('<shreddit-comment ')} challenge={'js_challenge' in r.text}")
