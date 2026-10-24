"""SQLite archive. Rows are added or refreshed; only `prune` deletes, by retention."""

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .parse import Comment, Post

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
  id TEXT PRIMARY KEY,
  subreddit TEXT NOT NULL,
  title TEXT NOT NULL,
  author TEXT,
  score INTEGER,
  upvote_ratio REAL,
  comment_count INTEGER,
  created TEXT,
  post_type TEXT,
  permalink TEXT NOT NULL,
  url TEXT,
  domain TEXT,
  flair TEXT,
  body_html TEXT,
  first_seen TEXT NOT NULL,
  last_seen_hot TEXT,
  thread_fetches INTEGER NOT NULL DEFAULT 0,
  first_thread_fetch TEXT,
  last_thread_fetch TEXT,
  pinned INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS posts_sub_seen ON posts (subreddit, last_seen_hot);

CREATE TABLE IF NOT EXISTS hot_snapshots (
  taken_at TEXT NOT NULL,
  subreddit TEXT NOT NULL,
  rank INTEGER NOT NULL,
  post_id TEXT NOT NULL,
  score INTEGER,
  comment_count INTEGER,
  PRIMARY KEY (taken_at, subreddit, rank)
);

CREATE TABLE IF NOT EXISTS comments (
  id TEXT PRIMARY KEY,
  post_id TEXT NOT NULL,
  parent_id TEXT,
  author TEXT,
  score INTEGER,
  created TEXT,
  permalink TEXT,
  body_html TEXT,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS comments_post ON comments (post_id);

CREATE TABLE IF NOT EXISTS fetch_log (
  at TEXT NOT NULL,
  method TEXT NOT NULL,
  url TEXT NOT NULL,
  status INTEGER,
  bytes INTEGER,
  ms INTEGER,
  ratelimit_remaining REAL,
  note TEXT
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path | str):
        self.db = sqlite3.connect(path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self._migrate()

    def _migrate(self):
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(posts)")}
        if "pinned" not in cols:
            with self.db:
                self.db.execute("ALTER TABLE posts ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")

    def close(self):
        self.db.close()

    def upsert_post(self, p: Post, seen_in_hot: bool, at: str | None = None):
        at = at or now()
        self.db.execute(
            """
            INSERT INTO posts (id, subreddit, title, author, score, upvote_ratio, comment_count, created,
                               post_type, permalink, url, domain, flair, body_html, first_seen, last_seen_hot)
            VALUES (:id, :subreddit, :title, :author, :score, :upvote_ratio, :comment_count, :created,
                    :post_type, :permalink, :url, :domain, :flair, :body_html, :at, :hot)
            ON CONFLICT (id) DO UPDATE SET
              title = excluded.title,
              score = coalesce(excluded.score, score),
              upvote_ratio = coalesce(excluded.upvote_ratio, upvote_ratio),
              comment_count = coalesce(excluded.comment_count, comment_count),
              flair = coalesce(excluded.flair, flair),
              url = coalesce(excluded.url, url),
              body_html = coalesce(excluded.body_html, body_html),
              last_seen_hot = coalesce(excluded.last_seen_hot, last_seen_hot)
            """,
            {**vars(p), "at": at, "hot": at if seen_in_hot else None},
        )

    def record_hot(self, subreddit: str, posts: list[Post], at: str | None = None):
        at = at or now()
        with self.db:
            for p in posts:
                self.upsert_post(p, seen_in_hot=True, at=at)
            self.db.executemany(
                "INSERT OR REPLACE INTO hot_snapshots VALUES (?, ?, ?, ?, ?, ?)",
                [(at, subreddit, i, p.id, p.score, p.comment_count) for i, p in enumerate(posts)],
            )

    def record_thread(self, post: Post | None, post_id: str, comments: list[Comment], at: str | None = None):
        at = at or now()
        with self.db:
            if post:
                self.upsert_post(post, seen_in_hot=False, at=at)
            self.db.executemany(
                """
                INSERT INTO comments (id, post_id, parent_id, author, score, created, permalink, body_html,
                                      first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (id) DO UPDATE SET
                  parent_id = coalesce(excluded.parent_id, parent_id),
                  author = coalesce(excluded.author, author),
                  score = coalesce(excluded.score, score),
                  body_html = coalesce(excluded.body_html, body_html),
                  last_seen = excluded.last_seen
                """,
                [
                    (c.id, c.post_id or post_id, c.parent_id, c.author, c.score, c.created, c.permalink,
                     c.body_html, at, at)
                    for c in comments
                ],
            )
            self.db.execute(
                """
                UPDATE posts SET thread_fetches = thread_fetches + 1,
                  first_thread_fetch = coalesce(first_thread_fetch, :at), last_thread_fetch = :at
                WHERE id = :id
                """,
                {"at": at, "id": post_id},
            )

    def threads_due(self, schedule_hours: list[float], hot_window_hours: float, at: str | None = None):
        """Posts seen in hot recently whose next thread fetch is due.

        Fetch 0 happens as soon as a post is seen; fetch i (i >= 1) once
        schedule_hours[i-1] hours have passed since fetch 0.
        """
        t = datetime.fromisoformat(at or now())
        rows = self.db.execute(
            """
            SELECT p.*, (SELECT count(*) FROM comments c WHERE c.post_id = p.id) AS archived_comments
            FROM posts p WHERE last_seen_hot >= ? ORDER BY last_seen_hot DESC, comment_count DESC
            """,
            ((t - timedelta(hours=hot_window_hours)).isoformat(timespec="seconds"),),
        ).fetchall()
        due = []
        for r in rows:
            n = r["thread_fetches"]
            if n == 0:
                due.append(r)
            elif r["archived_comments"] >= (r["comment_count"] or 0):
                continue  # nothing new to get
            elif n <= len(schedule_hours):
                first = datetime.fromisoformat(r["first_thread_fetch"])
                if t >= first + timedelta(hours=schedule_hours[n - 1]):
                    due.append(r)
        return due

    def log_fetch(self, method, url, status, nbytes, ms, remaining, note=None):
        with self.db:
            self.db.execute(
                "INSERT INTO fetch_log VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (now(), method, url, status, nbytes, ms, remaining, note),
            )

    def set_pinned(self, post_id: str, pinned: bool):
        with self.db:
            self.db.execute("UPDATE posts SET pinned = ? WHERE id = ?", (int(pinned), post_id))

    def prune(self, retention_days: int, log_days: int = 30, at: str | None = None) -> dict:
        """Delete posts last in hot (or fetched) more than `retention_days` ago, unless kept,
        with their comments; hot snapshots that old; and request log entries past `log_days`."""
        t = datetime.fromisoformat(at or now())
        out = {"posts": 0, "comments": 0}
        with self.db:
            if retention_days > 0:
                cutoff = (t - timedelta(days=retention_days)).isoformat(timespec="seconds")
                old = "SELECT id FROM posts WHERE pinned = 0 AND coalesce(last_seen_hot, last_thread_fetch, first_seen) < ?"
                out["comments"] = self.db.execute(f"DELETE FROM comments WHERE post_id IN ({old})", (cutoff,)).rowcount
                out["posts"] = self.db.execute(f"DELETE FROM posts WHERE id IN ({old})", (cutoff,)).rowcount
                self.db.execute("DELETE FROM hot_snapshots WHERE taken_at < ?", (cutoff,))
            log_cutoff = (t - timedelta(days=log_days)).isoformat(timespec="seconds")
            self.db.execute("DELETE FROM fetch_log WHERE at < ?", (log_cutoff,))
        return out

    # Reads for the web UI.

    def subreddits(self) -> list[str]:
        return [r[0] for r in self.db.execute("SELECT DISTINCT subreddit FROM posts ORDER BY lower(subreddit)")]

    def latest_hot(self, subreddit: str):
        """(taken_at, posts in rank order) of the newest hot snapshot."""
        row = self.db.execute(
            "SELECT max(taken_at) FROM hot_snapshots WHERE lower(subreddit) = lower(?)", (subreddit,)
        ).fetchone()
        if not row[0]:
            return None, []
        posts = self.db.execute(
            f"""
            SELECT p.*, h.rank, {self._archived} FROM hot_snapshots h JOIN posts p ON p.id = h.post_id
            WHERE h.taken_at = ? AND lower(h.subreddit) = lower(?) ORDER BY h.rank
            """,
            (row[0], subreddit),
        ).fetchall()
        return row[0], posts

    def older_posts(self, subreddit: str, exclude: list[str], limit: int, offset: int):
        """Archived posts not in the latest hot snapshot, most recently seen in hot first."""
        marks = ",".join("?" * len(exclude))
        return self.db.execute(
            f"""
            SELECT p.*, {self._archived} FROM posts p
            WHERE lower(p.subreddit) = lower(?) {f"AND p.id NOT IN ({marks})" if exclude else ""}
            ORDER BY p.last_seen_hot DESC, p.created DESC LIMIT ? OFFSET ?
            """,
            (subreddit, *exclude, limit, offset),
        ).fetchall()

    def post(self, post_id: str):
        return self.db.execute(f"SELECT p.*, {self._archived} FROM posts p WHERE p.id = ?", (post_id,)).fetchone()

    def comments(self, post_id: str):
        return self.db.execute("SELECT * FROM comments WHERE post_id = ?", (post_id,)).fetchall()

    def archived_ids(self, ids: list[str]) -> set[str]:
        if not ids:
            return set()
        marks = ",".join("?" * len(ids))
        return {r[0] for r in self.db.execute(f"SELECT id FROM posts WHERE id IN ({marks})", ids)}

    def recent_errors(self, limit: int = 10):
        return self.db.execute(
            "SELECT * FROM fetch_log WHERE status >= 400 OR note IS NOT NULL ORDER BY rowid DESC LIMIT ?", (limit,)
        ).fetchall()

    def last_hot_at(self) -> str | None:
        return self.db.execute("SELECT max(taken_at) FROM hot_snapshots").fetchone()[0]

    _archived = "(SELECT count(*) FROM comments c WHERE c.post_id = p.id) AS archived_comments"

    def stats(self) -> dict:
        q = lambda sql: self.db.execute(sql).fetchone()[0]
        return {
            "posts": q("SELECT count(*) FROM posts"),
            "posts_with_thread": q("SELECT count(*) FROM posts WHERE thread_fetches > 0"),
            "comments": q("SELECT count(*) FROM comments"),
            "hot_snapshots": q("SELECT count(DISTINCT taken_at || subreddit) FROM hot_snapshots"),
            "requests_last_hour": q(
                "SELECT count(*) FROM fetch_log WHERE at >= strftime('%Y-%m-%dT%H:%M:%S', 'now', '-1 hour')"
            ),
        }
