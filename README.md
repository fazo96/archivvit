# archivvit

A local, read-only archive of a few subreddits: their hot pages and the comments on those
posts, scraped without an account into SQLite and browsed from a small web UI.

Why and how Reddit can still be read logged out: [docs/reddit-access.md](docs/reddit-access.md).

## How it works

- `session.py` — headless Chromium (Playwright) passes Reddit's JS bot check once; the cookies
  are saved to `data/cookies.json` and reused until a request gets a 403 or the challenge page.
- `client.py` — `curl_cffi` with a Chrome TLS fingerprint and those cookies. One request at a
  time, 2–5 s apart, waits for the rate-limit window (~200 req / 10 min) when it runs low.
- `parse.py` — shreddit HTML (`<shreddit-post>`, `<shreddit-comment>`) to posts and comments; ads dropped.
- `scrape.py` — hot pages, then threads that are due: new in hot, then again 2 h and 12 h later
  (skipped when the archive already has every comment). Logged out, a thread shows only its first
  ~15 top-level comments, so it also loads the NEW and OLD orders, then "more replies" and
  "continue this thread", until the post's comment count is reached or `max_thread_requests` is spent.
- `store.py` — SQLite: `posts`, `hot_snapshots` (rank and score over time), `comments` (tree
  via `parent_id`), `fetch_log`.
- `web.py`, `templates/`, `static/` — Flask UI: hot list and older posts per subreddit, threads
  at Reddit's own paths (`/r/<sub>/comments/<id>/…`, so swapping the domain of a Reddit link works)
  with collapsible comments, and a Settings page. Reddit's HTML is sanitized with nh3; links to
  archived threads point to the local copy.
- `scheduler.py` — in `serve`, runs a pass every `scrape_interval_minutes` (or on "Run now"),
  re-reading the settings before each one.

## Use

```sh
nix develop
pytest
python -m archivvit serve            # web UI on 127.0.0.1:8080, scraping on the interval
python -m archivvit scrape           # one pass
python -m archivvit thread <url>
python -m archivvit stats
```

Everything lives in the data directory (`--data`, else `$ARCHIVVIT_DATA`, else `./data`):
`archive.sqlite`, `cookies.json` and `settings.json`. Settings are edited on the Settings page
(subreddits, scrape interval, re-fetch hours, request pacing); without a file the defaults in
`config.py` apply. The UI has no login: keep it on localhost or behind a private proxy.
`nix build` gives a wrapped `archivvit` with the headless browser.

## Known gaps

- Big threads are expensive: "continue this thread" pages often add only one comment each.
  83 comments took 68 extra requests; the default cap is 30, so large threads may be incomplete.
- Responses are ~500 KB of HTML each.
- No media download (images load from Reddit's image host, videos are links) or NixOS module yet.
