"""Capture raw server HTML fixtures and one more-comments POST as the browser sends it."""
import json, sys, time, pathlib, re, html
from playwright.sync_api import sync_playwright
from curl_cffi import requests as creq

OUT = pathlib.Path(sys.argv[1])
SUB = sys.argv[2] if len(sys.argv) > 2 else "NixOS"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
BASE = "https://www.reddit.com"
OUT.mkdir(parents=True, exist_ok=True)


def log(*a):
    print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)


posts_needed = []
captured = {}
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(user_agent=UA, locale="en-US", viewport={"width": 1280, "height": 900})
    page = ctx.new_page()

    def on_request(r):
        if "more-comments" in r.url and r.method == "POST" and "req" not in captured:
            captured["req"] = {"url": r.url, "headers": r.headers, "body": r.post_data}

    def on_response(r):
        if "more-comments" in r.url and "resp" not in captured:
            try:
                captured["resp"] = {"status": r.status, "headers": dict(r.headers), "body": r.text()}
            except Exception as e:
                captured["resp"] = {"error": str(e)}

    page.on("request", on_request)
    page.on("response", on_response)
    page.goto(f"{BASE}/r/{SUB}/hot/", wait_until="domcontentloaded")
    page.wait_for_selector("shreddit-post", timeout=20000)
    posts = page.eval_on_selector_all("shreddit-post", "els => els.map(e => [e.getAttribute('permalink'), +e.getAttribute('comment-count')])")
    thread = max(posts, key=lambda x: x[1])[0]
    page.goto(BASE + thread, wait_until="domcontentloaded")
    page.wait_for_selector("shreddit-comment", timeout=20000)
    btn = page.locator("faceplate-partial.more-comments-partial button")
    log("more-replies buttons:", btn.count())
    if btn.count():
        btn.first.scroll_into_view_if_needed()
        btn.first.click()
        page.wait_for_timeout(2500)
    cookies = ctx.cookies()
    browser.close()

(OUT / "more_comments_browser.json").write_text(json.dumps(captured, indent=1))
req = captured.get("req", {})
log("POST", req.get("url", "")[:120])
log("  content-type:", req.get("headers", {}).get("content-type"), "body:", (req.get("body") or "")[:300])
log("  extra headers:", sorted(k for k in req.get("headers", {}) if k not in ("user-agent", "accept-language", "referer", "accept")))
resp = captured.get("resp", {})
log("  response:", resp.get("status"), len(resp.get("body") or ""), "B, comments:", (resp.get("body") or "").count("<shreddit-comment "))

s = creq.Session(impersonate="chrome")
for c in cookies:
    s.cookies.set(c["name"], c["value"], domain=c["domain"], path=c["path"])


def get(name, url):
    time.sleep(2)
    r = s.get(url, timeout=30)
    (OUT / name).write_text(r.text)
    log(f"{name}: {r.status_code} {len(r.text)}B posts={r.text.count('<shreddit-post')} comments={r.text.count('<shreddit-comment ')}")
    return r.text


hot = get("hot.html", f"{BASE}/r/{SUB}/hot/")
m = re.search(r'src="(/svc/shreddit/community-more-posts/hot/[^"]+)"', hot)
if m:
    get("more_posts.html", BASE + html.unescape(m.group(1)))
th = get("thread.html", BASE + thread)
m = re.search(r'href="([^"]+/comment/[a-z0-9]+/\?force-legacy-sct=1)"[^>]*slot="more-comments-permalink"', th)
if m:
    get("continue_thread.html", BASE + html.unescape(m.group(1)))
else:
    log("no continue-thread link in raw thread HTML")

if req:
    time.sleep(2)
    csrf = next((c["value"] for c in cookies if c["name"] == "csrf_token"), "")
    body = req.get("body") or ""
    r = s.post(req["url"], data=body, headers={"content-type": req["headers"].get("content-type", "application/x-www-form-urlencoded")}, timeout=30)
    (OUT / "more_comments_http.html").write_text(r.text)
    log(f"replayed POST via curl_cffi: {r.status_code} {len(r.text)}B comments={r.text.count('<shreddit-comment ')} (csrf cookie present: {bool(csrf)})")
