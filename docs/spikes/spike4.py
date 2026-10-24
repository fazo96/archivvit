import json, sys, time, pathlib, re, html
from playwright.sync_api import sync_playwright
from curl_cffi import requests as creq

OUT = pathlib.Path(sys.argv[1])
SUB = sys.argv[2] if len(sys.argv) > 2 else "NixOS"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
posts = json.loads((OUT.parent / "out2" / "posts.json").read_text())
target = max(posts, key=lambda x: int(x.get("comment-count") or 0))
turl = "https://www.reddit.com" + target["permalink"]


def log(*a):
    print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(user_agent=UA, locale="en-US", viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    loads = []
    page.on("request", lambda r: loads.append((r.method, r.url[:150])) if "more-comments" in r.url or "/comments/" in r.url and "svc" in r.url else None)
    t = time.time()
    page.goto(turl, wait_until="domcontentloaded")
    page.wait_for_selector("shreddit-comment", timeout=15000)
    stable = 0
    last = -1
    for _ in range(60):
        page.keyboard.press("End")
        page.mouse.wheel(0, 8000)
        btn = page.locator("faceplate-partial.more-comments-partial button, button:has-text('View more comments'), button:has-text('more repl')")
        if btn.count():
            try:
                btn.first.scroll_into_view_if_needed(timeout=2000)
                btn.first.click(timeout=2000)
            except Exception:
                pass
        page.wait_for_timeout(800)
        n = page.locator("shreddit-comment").count()
        stable = stable + 1 if n == last else 0
        last = n
        if stable >= 4:
            break
    comments = page.eval_on_selector_all("shreddit-comment", "els => els.map(e => [e.getAttribute('thingid'), e.getAttribute('author'), !!e.querySelector('[slot=comment]')])")
    log(f"scroll+click: {len(comments)}/{target['comment-count']} comments, unique={len({c[0] for c in comments})}, "
        f"deleted-author={sum(1 for c in comments if c[1] in ('[deleted]', None))}, {time.time()-t:.1f}s")
    log("load requests seen:", len(loads))
    for m, u in loads[:6]:
        log("  ", m, u)
    cookies = ctx.cookies()
    browser.close()

# plain-HTTP pagination of hot via curl_cffi
s = creq.Session(impersonate="chrome")
for c in cookies:
    s.cookies.set(c["name"], c["value"], domain=c["domain"], path=c["path"])
r = s.get(f"https://www.reddit.com/r/{SUB}/hot/", timeout=30)
ids = re.findall(r'<shreddit-post[^>]*?\sid="(t3_[a-z0-9]+)"', r.text)
log(f"curl hot page 1: {r.status_code} posts={len(ids)}")
for i in range(3):
    m = re.search(r'src="(/svc/shreddit/community-more-posts/hot/[^"]+)"', r.text)
    if not m:
        log("no more-posts partial found")
        break
    time.sleep(2)
    r = s.get("https://www.reddit.com" + html.unescape(m.group(1)), timeout=30)
    new = re.findall(r'<shreddit-post[^>]*?\sid="(t3_[a-z0-9]+)"', r.text)
    ids += new
    log(f"curl more-posts {i+1}: {r.status_code} +{len(new)} posts (total unique {len(set(ids))}), ads={r.text.count('<shreddit-ad-post')}")
