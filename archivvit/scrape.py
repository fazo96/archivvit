import logging

from .client import Reddit
from .config import Config
from .parse import BASE, Comment, parse_comments, parse_listing, spelled_as
from .store import Store

log = logging.getLogger(__name__)

# Listing pages per subreddit before giving up on reaching posts_per_sub.
MAX_LISTING_PAGES = 4


def fetch_hot(reddit: Reddit, sub: str, limit: int):
    html = reddit.get(f"{BASE}/r/{sub}/hot/")
    listing = parse_listing(html)
    if not listing.posts:
        name = spelled_as(html, sub)
        if name:
            log.warning("r/%s is spelled r/%s on Reddit; use that in Settings", sub, name)
            listing = parse_listing(reddit.get(f"{BASE}/r/{name}/hot/"))
        if not listing.posts:
            raise RuntimeError(f"r/{sub}: no posts on the hot page (does the subreddit exist?)")
    posts, seen = [], set()
    for page in range(MAX_LISTING_PAGES):
        if page:
            listing = parse_listing(reddit.get(url))
        for p in listing.posts:
            if p.id not in seen:
                seen.add(p.id)
                posts.append(p)
        if len(posts) >= limit or not listing.next_url:
            break
        url = listing.next_url
    return posts[:limit]


# Logged out, a thread shows only its first ~15 top-level comments, with no way to page
# further. Re-rendering the tree in other orders surfaces the rest.
EXTRA_SORTS = ("NEW", "OLD")

# Loader kinds, in the order they are worth spending requests on.
SORT, MORE, CONTINUE = range(3)


def fetch_thread(reddit: Reddit, permalink: str, max_requests: int):
    """The thread page, then other sort orders, "more replies" and "continue this thread",
    until the post's comment count is reached or `max_requests` extra requests are spent."""
    frag = parse_comments(reddit.get(BASE + permalink))
    if frag.post is None:
        raise RuntimeError(f"{permalink}: no post on the thread page")
    post = frag.post
    sub, short_id = post.subreddit, post.id.removeprefix("t3_")
    comments: dict[str, Comment] = {}
    queues = ([], [], [])
    queued = set()

    def enqueue(kind, method, url, cursor=None):
        # "Continue this thread" pages are the same whatever sort led to them.
        key = url if kind == CONTINUE else (url, cursor)
        if key not in queued:
            queued.add(key)
            queues[kind].append((method, url, cursor))

    def add(f):
        for c in f.comments:
            prev = comments.get(c.id)
            # A continue-thread page roots its subtree at depth 0 with no parent; keep what we knew.
            if prev and c.parent_id is None:
                c.parent_id = prev.parent_id
            comments[c.id] = c
        for m in f.more:
            enqueue(MORE, "POST", m.url, m.cursor)
        for u in f.continue_urls:
            enqueue(CONTINUE, "GET", u)

    add(frag)
    for sort in EXTRA_SORTS:
        enqueue(SORT, "GET", f"{BASE}/svc/shreddit/comments/r/{sub}/{short_id}?seeker-session=true&render-mode=partial&sort={sort}")

    expected = post.comment_count or 0
    requests = 0
    while requests < max_requests and len(comments) < expected:
        queue = next((q for q in queues if q), None)
        if queue is None:
            break
        method, url, cursor = queue.pop(0)
        requests += 1
        html = reddit.post(url, {"cursor": cursor}) if method == "POST" else reddit.get(url)
        f = parse_comments(html)
        before = len(comments)
        add(f)
        log.debug("%s %s: +%d new of %d, %d more, %d continue", method, url[-60:], len(comments) - before,
                  len(f.comments), len(f.more), len(f.continue_urls))
    left = sum(map(len, queues))
    if len(comments) < expected and left:
        log.warning("%s: %d/%d comments, stopped with %d loaders left (max_thread_requests=%d)",
                    permalink, len(comments), expected, left, max_requests)
    return post, list(comments.values()), requests


def scrape(cfg: Config, store: Store, reddit: Reddit) -> dict:
    summary = {"hot_posts": 0, "threads": 0, "comments": 0, "errors": []}
    for sub in cfg.subreddits:
        try:
            posts = fetch_hot(reddit, sub, cfg.posts_per_sub)
        except RuntimeError as e:
            log.error("r/%s: %s", sub, e)
            summary["errors"].append(f"r/{sub}: {e}")
            continue
        store.record_hot(sub, posts)
        summary["hot_posts"] += len(posts)
        log.info("r/%s: %d hot posts", sub, len(posts))

    due = store.threads_due(cfg.refetch_hours, cfg.hot_window_hours)
    log.info("%d threads due", len(due))
    for i, row in enumerate(due, 1):
        try:
            post, comments, extra = fetch_thread(reddit, row["permalink"], cfg.max_thread_requests)
        except RuntimeError as e:
            log.error("%s", e)
            summary["errors"].append(str(e))
            continue
        store.record_thread(post, row["id"], comments)
        summary["threads"] += 1
        summary["comments"] += len(comments)
        log.info(
            "[%d/%d] %s: %d/%s comments (+%d requests) %s",
            i, len(due), row["id"], len(comments), post.comment_count, extra, post.title[:50],
        )
    summary["requests"] = reddit.requests
    summary["pruned"] = store.prune(cfg.retention_days)
    if summary["pruned"]["posts"]:
        log.info("retention: deleted %(posts)d posts and %(comments)d comments", summary["pruned"])
    return summary
