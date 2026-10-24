import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import nh3
from flask import Flask, abort, flash, g, redirect, render_template, request, url_for
from markupsafe import Markup

from . import config
from .store import Store

PAGE_SIZE = 25
HOME_PAGE_SIZE = 50
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


def create_app(data_dir: Path, scheduler=None) -> Flask:
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

    @app.context_processor
    def nav():
        if request.endpoint == "static":
            return {}
        subs = list(cfg().subreddits)
        known = {s.lower() for s in subs}
        subs += [s for s in store().subreddits() if s.lower() not in known]
        return {"nav_subs": subs}

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
    def subreddit(sub):
        page = max(request.args.get("page", 1, type=int), 1)
        taken_at, hot = store().latest_hot(sub)
        older = store().older_posts(sub, [p["id"] for p in hot], PAGE_SIZE + 1, (page - 1) * PAGE_SIZE)
        if not hot and not older and page == 1 and sub.lower() not in {s.lower() for s in cfg().subreddits}:
            abort(404)
        return render_template(
            "subreddit.html", sub=sub, taken_at=taken_at, hot=hot if page == 1 else [],
            older=older[:PAGE_SIZE], page=page, has_next=len(older) > PAGE_SIZE,
        )

    @app.get("/r/<sub>/comments/<pid>/")
    @app.get("/r/<sub>/comments/<pid>/<path:slug>")
    def thread(sub, pid, slug=None):
        post = store().post("t3_" + pid)
        if post is None:
            abort(404)
        sort = request.args.get("sort", "top")
        rows = store().comments(post["id"])
        render = Renderer(store().archived_ids(Renderer.linked_ids(post["body_html"], *(r["body_html"] for r in rows))))
        return render_template(
            "thread.html", post=post, tree=comment_tree(rows, sort), sort=sort, render=render,
            reddit_url="https://www.reddit.com" + post["permalink"],
        )

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
