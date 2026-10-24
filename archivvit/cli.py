import argparse
import json
import logging
import re

from . import config, scrape
from .client import Reddit
from .store import Store


def main():
    ap = argparse.ArgumentParser(prog="archivvit", description="Local read-only Reddit archive")
    ap.add_argument("-c", "--config", help="TOML config (default: $ARCHIVVIT_CONFIG, else built-in defaults)")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scrape", help="fetch hot pages and the threads that are due")
    t = sub.add_parser("thread", help="fetch one thread now (URL or permalink)")
    t.add_argument("url")
    sub.add_parser("session", help="get fresh cookies from the headless browser")
    sub.add_parser("stats", help="archive counts")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    for noisy in ("asyncio", "curl_cffi"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    cfg = config.load(args.config)
    store = Store(cfg.db_path)
    reddit = Reddit(cfg, store)
    try:
        if args.cmd == "scrape":
            scrape.scrape(cfg, store, reddit)
        elif args.cmd == "thread":
            m = re.search(r"(/r/[^/]+/comments/([a-z0-9]+)/[^?#]*)", args.url)
            if not m:
                ap.error("not a Reddit thread URL")
            post, comments, extra = scrape.fetch_thread(reddit, m.group(1), cfg.max_thread_requests)
            store.record_thread(post, post.id, comments)
            print(f"{post.title}: {len(comments)}/{post.comment_count} comments, {extra} extra requests")
        elif args.cmd == "session":
            reddit._connect(fresh=True)
            print(f"cookies saved to {cfg.cookies_path}")
        elif args.cmd == "stats":
            print(json.dumps(store.stats(), indent=1))
    finally:
        store.close()
