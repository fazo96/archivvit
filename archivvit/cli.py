import argparse
import json
import logging
import re

from . import config, scrape, session
from .client import Reddit
from .store import Store


def main():
    ap = argparse.ArgumentParser(prog="archivvit", description="Local read-only Reddit archive")
    ap.add_argument("-d", "--data", help="data directory (default: $ARCHIVVIT_DATA, else ./data)")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scrape", help="fetch hot pages and the threads that are due")
    t = sub.add_parser("thread", help="fetch one thread now (URL or permalink)")
    t.add_argument("url")
    se = sub.add_parser("session", help="get fresh cookies from the headless browser")
    se.add_argument("--url", help="page to pass the bot check on (default: the first subreddit); "
                                  "with this, cookies are printed instead of saved")
    sub.add_parser("stats", help="archive counts")
    s = sub.add_parser("serve", help="web UI, scraping on the configured interval")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8080)
    s.add_argument("--no-scheduler", action="store_true", help="only serve the UI")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    for noisy in ("asyncio", "curl_cffi"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    cfg = config.load(args.data)
    if args.cmd == "serve":
        return serve(cfg, args)
    store = Store(cfg.db_path)
    reddit = Reddit(cfg, store)
    try:
        if args.cmd == "scrape":
            print(json.dumps(scrape.scrape(cfg, store, reddit), indent=1))
        elif args.cmd == "thread":
            m = re.search(r"(/r/[^/]+/comments/([a-z0-9]+)/[^?#]*)", args.url)
            if not m:
                ap.error("not a Reddit thread URL")
            post, comments, extra = scrape.fetch_thread(reddit, m.group(1), cfg.max_thread_requests)
            store.record_thread(post, post.id, comments)
            print(f"{post.title}: {len(comments)}/{post.comment_count} comments, {extra} extra requests")
        elif args.cmd == "session":
            if args.url:
                cookies = session.browser_cookies(cfg.user_agent, args.url)
                print(" ".join(sorted(c["name"] for c in cookies)))
            else:
                reddit._connect(fresh=True)
                print(f"cookies saved to {cfg.cookies_path}")
        elif args.cmd == "stats":
            print(json.dumps(store.stats(), indent=1))
    finally:
        store.close()


def serve(cfg, args):
    import waitress

    from .jobs import Fetcher
    from .scheduler import Scheduler
    from .web import create_app

    # One client for scheduled passes and on-demand fetches, so they share the pacing.
    reddit = Reddit(cfg, Store(cfg.db_path, threadsafe=True))
    fetcher = Fetcher(cfg.data_dir, reddit)
    fetcher.start()
    scheduler = None
    if not args.no_scheduler:
        scheduler = Scheduler(cfg.data_dir, reddit)
        scheduler.start()
    logging.getLogger(__name__).info("serving on http://%s:%d", args.host, args.port)
    waitress.serve(create_app(cfg.data_dir, scheduler, fetcher), host=args.host, port=args.port, threads=4)
