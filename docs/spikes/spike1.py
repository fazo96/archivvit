import json, sys, time, pathlib
from playwright.sync_api import sync_playwright

OUT = pathlib.Path(sys.argv[1])
SUB = sys.argv[2] if len(sys.argv) > 2 else "NixOS"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"


def log(*a):
    print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    log("default UA:", browser.new_page().evaluate("navigator.userAgent"))
    ctx = browser.new_context(user_agent=UA, locale="en-US", viewport={"width": 1280, "height": 900})
    page = ctx.new_page()

    responses = []
    page.on("response", lambda r: responses.append((r.status, r.url[:140], {k: v for k, v in r.headers.items() if k.startswith("x-ratelimit")})))

    t = time.time()
    resp = page.goto(f"https://www.reddit.com/r/{SUB}/", wait_until="domcontentloaded")
    log("listing first response", resp.status, "url", page.url)
    try:
        page.wait_for_selector("shreddit-post", timeout=20000)
    except Exception as e:
        log("no shreddit-post:", e)
    log(f"listing loaded in {time.time()-t:.1f}s, title={page.title()!r}")
    (OUT / "listing.html").write_text(page.content())
    posts = page.eval_on_selector_all(
        "shreddit-post",
        "els => els.map(e => Object.fromEntries([...e.attributes].map(a => [a.name, a.value.slice(0, 200)])))",
    )
    log("shreddit-post count:", len(posts))
    (OUT / "posts.json").write_text(json.dumps(posts, indent=1))
    if posts:
        log("post attribute names:", sorted(posts[0].keys()))

    doc_reqs = [r for r in responses if "reddit.com" in r[1] and not any(x in r[1] for x in ("redditstatic", "redd.it"))]
    log("reddit requests during listing:", len(doc_reqs))
    for r in doc_reqs[:15]:
        log("  ", r)

    (OUT / "cookies.json").write_text(json.dumps(ctx.cookies(), indent=1))
    log("cookie names:", sorted({c["name"] for c in ctx.cookies()}))
    browser.close()
