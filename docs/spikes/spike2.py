import json, sys, time, pathlib, re, html
from collections import Counter
from playwright.sync_api import sync_playwright
import requests

OUT = pathlib.Path(sys.argv[1])
SUB = sys.argv[2] if len(sys.argv) > 2 else "NixOS"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
ATTRS = "els => els.map(e => Object.fromEntries([...e.attributes].map(a => [a.name, a.value.slice(0, 300)])))"


def log(*a):
    print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(user_agent=UA, locale="en-US", viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    rl = []
    page.on("response", lambda r: rl.append((r.status, r.url[:110], r.headers.get("x-ratelimit-remaining"))) if "reddit.com/" in r.url and ("/r/" in r.url or "more-" in r.url or ".json" in r.url) else None)

    # 1. hot listing + infinite scroll
    t = time.time()
    page.goto(f"https://www.reddit.com/r/{SUB}/hot/", wait_until="domcontentloaded")
    page.wait_for_selector("shreddit-post", timeout=20000)
    for _ in range(8):
        n = page.locator("shreddit-post").count()
        if n >= 25:
            break
        page.mouse.wheel(0, 6000)
        page.wait_for_timeout(1500)
    posts = page.eval_on_selector_all("shreddit-post", ATTRS)
    log(f"hot: {len(posts)} posts after scrolling, {time.time()-t:.1f}s")
    ads = page.locator("shreddit-ad-post").count()
    log("ad posts in DOM:", ads)
    (OUT / "posts.json").write_text(json.dumps(posts, indent=1))
    for x in posts[:5]:
        log("  ", x.get("score"), x.get("comment-count"), x.get("post-type"), x.get("post-title", "")[:60])

    # 2. a big thread
    target = max(posts, key=lambda x: int(x.get("comment-count") or 0))
    turl = "https://www.reddit.com" + target["permalink"]
    log("thread:", turl, "comment-count", target.get("comment-count"))
    t = time.time()
    page.goto(turl, wait_until="domcontentloaded")
    page.wait_for_selector("shreddit-comment", timeout=20000)
    page.wait_for_timeout(2000)
    comments = page.eval_on_selector_all("shreddit-comment", ATTRS)
    log(f"thread: {len(comments)} shreddit-comment in DOM, {time.time()-t:.1f}s")
    if comments:
        log("comment attribute names:", sorted(comments[0].keys()))
        log("depth histogram:", sorted(Counter(c.get("depth") for c in comments).items()))
    bodies = page.eval_on_selector_all("shreddit-comment", "els => els.slice(0,3).map(e => { const b = e.querySelector('[slot=comment]'); return b ? b.innerText.slice(0,120) : null })")
    log("first bodies:", bodies)
    post_body = page.eval_on_selector("shreddit-post", "e => { const b = e.querySelector('[slot=text-body]'); return b ? b.innerText.slice(0,200) : null }")
    log("post body:", post_body)
    partials = page.eval_on_selector_all("faceplate-partial", "els => els.map(e => e.getAttribute('src')).filter(s => s && /comment/.test(s))")
    log(f"comment-related partials (more replies): {len(partials)}")
    for s in partials[:4]:
        log("  ", s[:200])
    (OUT / "thread.html").write_text(page.content())
    (OUT / "comments.json").write_text(json.dumps(comments, indent=1))

    # 3. .json from inside the browser context
    for path in [f"/r/{SUB}/hot.json?limit=5", target["permalink"] + ".json?limit=5"]:
        r = page.evaluate("async u => { const r = await fetch(u); const t = await r.text(); return [r.status, r.headers.get('content-type'), t.slice(0,80)] }", path)
        log("in-browser fetch", path[:60], r)

    cookies = ctx.cookies()
    browser.close()

log("browser ratelimit trail:", [x for x in rl if x[2]][-3:])

# 4. reuse cookies from plain HTTP
s = requests.Session()
s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
for c in cookies:
    s.cookies.set(c["name"], c["value"], domain=c["domain"], path=c["path"])
listing = None
for name, url in [
    ("hot html", f"https://www.reddit.com/r/{SUB}/hot/"),
    ("thread html", turl),
    ("hot .json", f"https://www.reddit.com/r/{SUB}/hot.json?limit=5"),
]:
    time.sleep(2)
    r = s.get(url, timeout=30)
    n_posts, n_comm = r.text.count("<shreddit-post"), r.text.count("<shreddit-comment ")
    challenge = "js_challenge" in r.text or "solution" in r.text[:5000]
    log(f"requests {name}: {r.status_code} {len(r.text)}B posts={n_posts} comments={n_comm} challenge={challenge} rl-remaining={r.headers.get('x-ratelimit-remaining')}")
    if name == "hot html":
        listing = r.text

m = re.search(r'src="(/svc/shreddit/community-more-posts/hot/[^"]+)"', listing or "")
if m:
    time.sleep(2)
    r = s.get("https://www.reddit.com" + html.unescape(m.group(1)), timeout=30)
    log(f"requests more-posts partial: {r.status_code} {len(r.text)}B posts={r.text.count('<shreddit-post')}")

# 5. no cookies at all, plain HTTP
time.sleep(2)
r = requests.get(turl, headers={"User-Agent": UA}, timeout=30)
log(f"cookieless requests thread: {r.status_code} {len(r.text)}B comments={r.text.count('<shreddit-comment ')}")
