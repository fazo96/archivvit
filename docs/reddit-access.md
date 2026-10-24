# Reddit access findings (2026-10-23)

Tested from Ada (residential IP) after redlib stopped working. Spike scripts are in
`docs/spikes/` (run with `docs/spikes/run.sh <script> <outdir> <sub>`; `spike3`/`spike4`
read `../out2/posts.json` from `spike2`).

## What works

| Approach | Result |
|---|---|
| curl / Python `requests`, no cookies | 200 but only a ~8 KB JS challenge page (`?solution=…&js_challenge=1&jsc_token=…`) |
| `*.json` endpoints | **403 "blocked by network security"** from every client, including in-page `fetch` in a real browser |
| `old.reddit.com` | 302 to `/login/?reason=lor2` |
| Headless Chromium (Playwright) | **Works.** Challenge auto-solves, `/r/X/hot/` loads in ~1.4 s. Default HeadlessChrome UA not needed but a desktop Chrome UA was used |
| Browser cookies + Python `requests` | **403** — cookies are valid, so the TLS/HTTP2 fingerprint is checked |
| Browser cookies + `curl_cffi` (`impersonate="chrome"`) | **Works** — full HTML for listings, more-posts partials and threads |
| `curl_cffi` without cookies | 403 / no content: a browser pass is required to get cookies |
| RSS (`/r/X/.rss`, `/comments/…/.rss`), anonymous plain curl | Works, ~**1 req/min** budget. Thread RSS is flat (no tree, no scores) but contains **every** comment |

Cookies set by the challenge: `csrf_token csv edgebucket loid session_tracker token_v2`.
Cookie lifetime not measured yet.

## Page structure (new Reddit, "shreddit")

- Default subreddit sort is **best**; use `/r/X/hot/`.
- Listing: `<shreddit-post>` attributes carry `id` (t3_…), `post-title`, `score`, `upvote-ratio`,
  `comment-count`, `author`, `author-id`, `created-timestamp`, `post-type` (text/link/image/gallery/video),
  `permalink`, `content-href`, `domain`, `subreddit-name`. Text body is in `[slot=text-body]`.
- Ads are separate `<shreddit-ad-post>` elements (~3 per page) — drop them.
- Pagination: the page HTML contains
  `src="/svc/shreddit/community-more-posts/hot/?after=…&t=DAY&name=X&…"`; GET it (HTML partial, ~25 posts),
  each partial contains the next one. Page 1 over plain HTTP has only 3 posts; 4 requests → 77 posts.
- Thread: `<shreddit-comment>` attributes `thingid`, `parentid`, `depth`, `author`, `score`, `created`,
  `permalink`, `postid`; body in `[slot=comment]`. Initial render ~25 comments.
- "N more replies": `<faceplate-partial class="more-comments-partial" method="post"
  src="/svc/shreddit/more-comments/<sub>/t3_<id>?sort=CONFIDENCE&startingDepth=…&commentParentPositions=…">`
  — a **POST** (presumably needs `csrf_token`); not yet reproduced outside the browser.
- Deep chains are cut off with "continue this thread" links:
  `<a slot="more-comments-permalink" href="/r/<sub>/comments/<id>/comment/<cid>/?force-legacy-sct=1">`.
  Clicking every more-replies control in the browser gave 50/83 comments; the missing 33 were all
  in these deep chains (verified by diffing against thread RSS). Following the permalinks should
  recover them — not yet verified.

## Rate limits

- HTML endpoints: `x-ratelimit-*` headers show ~**200 requests per ~600 s window**.
  A browser page load costs 3–8 (background XHRs count); a `curl_cffi` request costs 1.
- ~120 requests over ~5 min produced no blocks.
- Estimate for 5 subs × (2 listing pages + 25 threads × ~4 pages) ≈ 500 req per full refresh
  → fine if spread over an hour.

## Recommended approach

1. Headless Chromium only to obtain cookies (~2 s); redo on 403 / expiry.
2. `curl_cffi` (or another Chrome-TLS-impersonating client) for listings, pagination, threads and
   continue-thread permalinks.
3. more-replies POST: reproduce over HTTP with the CSRF token, otherwise expand those threads in the browser.
4. RSS as a cross-check/fallback for comment completeness.

Risk: markup or fingerprinting changes (same failure mode as redlib).
