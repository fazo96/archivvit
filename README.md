# archivvit

A local, read-only archive of a few subreddits: their hot pages and the comments on those
posts, scraped without an account into SQLite, to be browsed from a small web UI (not built yet).

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

## Use

```sh
nix develop
pytest
python -m archivvit -c archivvit.toml scrape    # one pass
python -m archivvit -c archivvit.toml thread <url>
python -m archivvit -c archivvit.toml stats
```

Config is TOML, see [archivvit.toml.example](archivvit.toml.example); every key is optional.
`nix build` gives a wrapped `archivvit` with the headless browser.

## Known gaps

- Big threads are expensive: "continue this thread" pages often add only one comment each.
  83 comments took 68 extra requests; the default cap is 30, so large threads may be incomplete.
- Responses are ~500 KB of HTML each.
- No media download, web UI, systemd timer or NixOS module yet.
