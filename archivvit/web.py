import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import nh3
from flask import Flask, abort, flash, g, redirect, render_template, request, url_for
from markupsafe import Markup

from . import config
from .jobs import COMMENT, HOT, THREAD
from .parse import BASE
from .store import Store

PAGE_SIZE = 25
HOME_PAGE_SIZE = 50
ID = re.compile(r"[a-z0-9]{1,16}")
SUBREDDIT = re.compile(r"[A-Za-z0-9_]{2,21}")
THREAD_LINK = re.compile(r"^(?:https?://(?:www\.|old\.|new\.|np\.)?reddit\.com)?/r/([^/]+)/comments/([a-z0-9]+)")


def ago(ts) -> str:
    if not ts:
        return ""
    t = ts if isinstance(ts, datetime) else _parse(ts)
    if t is None:
        return ""
    s = int((datetime.now(timezone.utc) - t).total_seconds())
    future, s = s < 0, abs(s)
    for unit, n in (("d", 86400), ("h", 3600), ("min", 60)):
        if s >= n:
            text = f"{s // n} {unit}"
            break
    else:
        text = "moments" if not future else "a moment"
    return f"in {text}" if future else f"{text} ago"


def _parse(ts: str):
    try:
        t = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


class Renderer:
    """Sanitizes Reddit's HTML; thread links we have archived point here, other relative links to Reddit."""

    def __init__(self, local: set[str]):
        self.local = local

    @staticmethod
    def linked_ids(*htmls) -> list[str]:
        ids = set()
        for h in htmls:
            for href in re.findall(r'href="([^"]+)"', h or ""):
                m = THREAD_LINK.match(href)
                if m:
                    ids.add("t3_" + m.group(2))
        return sorted(ids)

    def _attr(self, element, attribute, value):
        if element == "a" and attribute == "href":
            m = THREAD_LINK.match(value)
            if m and "t3_" + m.group(2) in self.local:
                return f"/r/{m.group(1)}/comments/{m.group(2)}/"
            if value.startswith("/"):
                return "https://www.reddit.com" + value
        return value

    def __call__(self, html: str | None) -> Markup:
        if not html:
            return Markup("")
        return Markup(nh3.clean(
            html, attribute_filter=self._attr, link_rel="noopener noreferrer nofollow",
            allowed_classes={"span": {"md-spoiler-text"}},
        ))


def focus_comment(slug: str | None) -> str | None:
    """The comment a thread URL points at: .../comment/<cid>/ (current) or .../<slug>/<cid>/ (old)."""
    parts = [p for p in (slug or "").split("/") if p]
    if len(parts) >= 2 and ID.fullmatch(parts[1]):
        return parts[1]
    return None


def interleave(lists):
    """Each list's first item, then each one's second, and so on."""
    out = []
    for i in range(max(map(len, lists), default=0)):
        out += [lst[i] for lst in lists if i < len(lst)]
    return out


def comment_tree(rows, sort: str):
    ids = {r["id"] for r in rows}
    children = defaultdict(list)
    for r in rows:
        children[r["parent_id"] if r["parent_id"] in ids else None].append(r)
    if sort == "new":
        key, rev = (lambda r: r["created"] or ""), True
    elif sort == "old":
        key, rev = (lambda r: r["created"] or ""), False
    else:
        key, rev = (lambda r: r["score"] if r["score"] is not None else 0), True

    def build(parent):
        return [{"c": r, "children": build(r["id"])} for r in sorted(children[parent], key=key, reverse=rev)]

    return build(None)


def create_app(data_dir: Path, scheduler=None, fetcher=None) -> Flask:
    app = Flask(__name__)
    app.secret_key = "archivvit"  # only signs flash messages
    app.jinja_env.filters["ago"] = ago
    app.jinja_env.trim_blocks = True
    app.jinja_env.lstrip_blocks = True

    def cfg():
        if "cfg" not in g:
            g.cfg = config.load(data_dir)
        return g.cfg

    def store() -> Store:
        if "store" not in g:
            g.store = Store(cfg().db_path)
        return g.store

    @app.teardown_appcontext
    def close(_):
        s = g.pop("store", None)
        if s:
            s.close()

    def job(kind, target):
        return fetcher.status(kind, target) if fetcher else None

    @app.before_request
    def same_origin():
        # Every POST changes something or makes requests to Reddit: refuse ones sent by other sites.
        origin = request.headers.get("Origin")
        if request.method == "POST" and origin and urlsplit(origin).netloc != request.host:
            abort(403)

    @app.context_processor
    def nav():
        if request.endpoint == "static":
            return {}
        # Only the followed ones: one-off fetches of other subreddits stay out of the header.
        return {"nav_subs": list(cfg().subreddits)}

    @app.get("/")
    def index():
        subs = cfg().subreddits or store().subreddits()
        if not subs:
            return redirect(url_for("settings"))
        page = max(request.args.get("page", 1, type=int), 1)
        hot = [store().latest_hot(s) for s in subs]
        posts = interleave([posts for _, posts in hot])
        start = (page - 1) * HOME_PAGE_SIZE
        return render_template(
            "home.html", posts=posts[start:start + HOME_PAGE_SIZE], start=start, page=page,
            has_next=len(posts) > start + HOME_PAGE_SIZE,
            taken_at=max((t for t, _ in hot if t), default=None), n_subs=sum(1 for _, p in hot if p),
        )

    @app.get("/r/<sub>/")
    @app.get("/r/<sub>/<any(hot, new, top, rising, best, controversial):sort>/")
    def subreddit(sub, sort=None):
        if not SUBREDDIT.fullmatch(sub):
            abort(404)
        if sort:
            return redirect(url_for("subreddit", sub=sub))
        page = max(request.args.get("page", 1, type=int), 1)
        taken_at, hot = store().latest_hot(sub)
        older = store().older_posts(sub, [p["id"] for p in hot], PAGE_SIZE + 1, (page - 1) * PAGE_SIZE)
        return render_template(
            "subreddit.html", sub=sub, taken_at=taken_at, hot=hot if page == 1 else [],
            older=older[:PAGE_SIZE], page=page, has_next=len(older) > PAGE_SIZE,
            followed=sub.lower() in {s.lower() for s in cfg().subreddits}, job=job(HOT, sub),
        )

    @app.get("/r/<sub>/comments/<pid>/")
    @app.get("/r/<sub>/comments/<pid>/<path:slug>")
    def thread(sub, pid, slug=None):
        if not ID.fullmatch(pid):
            abort(404)
        post = store().post("t3_" + pid)
        if post is None:
            return render_template("missing.html", pid=pid, sub=sub, job=job(THREAD, pid))
        sort = request.args.get("sort", "top")
        rows = store().comments(post["id"])
        focus = focus_comment(slug)
        focus_id = f"t1_{focus}" if focus else None
        render = Renderer(store().archived_ids(Renderer.linked_ids(post["body_html"], *(r["body_html"] for r in rows))))
        return render_template(
            "thread.html", post=post, tree=comment_tree(rows, sort), sort=sort, render=render,
            gallery=json.loads(post["gallery"]) if post["gallery"] else None,
            reddit_url="https://www.reddit.com" + post["permalink"], focus=focus, focus_id=focus_id,
            focus_missing=bool(focus_id) and focus_id not in {r["id"] for r in rows},
            thread_job=job(THREAD, pid), comment_job=job(COMMENT, f"{pid}/{focus}") if focus else None,
        )

    @app.get("/comments/<pid>/")
    @app.get("/comments/<pid>/<path:slug>")
    def short_thread(pid, slug=None):
        """reddit.com/comments/<id>/ has no subreddit: send archived posts to their full path."""
        if not ID.fullmatch(pid):
            abort(404)
        post = store().post("t3_" + pid)
        if post:
            return redirect(f"/r/{post['subreddit']}/comments/{pid}/{slug or ''}")
        return render_template("missing.html", pid=pid, sub=None, job=job(THREAD, pid))

    @app.get("/gallery/<pid>")
    @app.get("/gallery/<pid>/")
    def gallery(pid):
        return redirect(url_for("short_thread", pid=pid))

    @app.get("/r/<sub>/s/<code>")
    @app.get("/r/<sub>/s/<code>/")
    def share(sub, code):
        """Share links (from Reddit's app) only redirect to the real permalink: ask Reddit where."""
        if not fetcher or not SUBREDDIT.fullmatch(sub) or not re.fullmatch(r"[A-Za-z0-9]{1,32}", code):
            abort(404)
        try:
            target = urlsplit(fetcher.reddit.location(f"{BASE}/r/{sub}/s/{code}")).path
        except Exception as e:
            return render_template("unsupported.html", message=f"Reddit did not resolve this share link ({e})."), 502
        if not THREAD_LINK.match(target):
            return render_template("unsupported.html", message="This share link does not point to a post."), 404
        return redirect(target)

    @app.post("/fetch")
    def fetch():
        kind, target = request.form.get("kind", ""), request.form.get("target", "")
        valid = {
            THREAD: ID.fullmatch(target),
            COMMENT: re.fullmatch(r"[a-z0-9]{1,16}/[a-z0-9]{1,16}", target),
            HOT: SUBREDDIT.fullmatch(target),
        }.get(kind)
        if not valid:
            abort(400)
        if fetcher:
            fetcher.submit(kind, target)
        else:
            flash("Fetching is off in this process.", "error")
        nxt = request.form.get("next", "/")
        return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else "/")

    @app.post("/subreddits/add")
    def add_subreddit():
        sub = request.form.get("sub", "")
        if not SUBREDDIT.fullmatch(sub):
            abort(400)
        c = cfg()
        if sub.lower() not in {s.lower() for s in c.subreddits}:
            c.subreddits.append(sub)
            config.save(c)
            if scheduler:
                scheduler.reschedule()
            flash(f"Added r/{sub}: its hot page and threads are scraped from the next pass.")
        return redirect(url_for("subreddit", sub=sub))

    @app.errorhandler(404)
    def not_found(_):
        return render_template("unsupported.html", message=None), 404

    @app.post("/r/<sub>/comments/<pid>/keep")
    def keep(sub, pid):
        post = store().post("t3_" + pid)
        if post is None:
            abort(404)
        store().set_pinned(post["id"], request.form.get("keep") == "1")
        return redirect(url_for("thread", sub=post["subreddit"], pid=pid))

    @app.route("/settings", methods=["GET", "POST"])
    def settings():
        c = cfg()
        form = None
        if request.method == "POST":
            form = request.form
            errors = config.update(c, form)
            if not errors:
                config.save(c)
                if scheduler:
                    scheduler.reschedule()
                flash("Settings saved.")
                return redirect(url_for("settings"))
            for e in errors:
                flash(e, "error")
        return render_template(
            "settings.html", cfg=c, form=form, scheduler=scheduler,
            stats=store().stats(), errors=store().recent_errors(),
        )

    @app.post("/run")
    def run_now():
        if scheduler:
            scheduler.trigger()
            flash("Scrape pass started." if not scheduler.running_since else "A pass is running; another will follow.")
        else:
            flash("The scheduler is off in this process.", "error")
        return redirect(url_for("settings"))

    return app
