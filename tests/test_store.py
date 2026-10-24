from archivvit.parse import Comment, Post
from archivvit.store import Store


def post(pid="t3_a", **kw):
    base = dict(id=pid, subreddit="NixOS", title="t", author="u", score=1, upvote_ratio=0.9, comment_count=3,
                created=None, post_type="text", permalink=f"/r/NixOS/comments/{pid[3:]}/t/", url=None, domain=None)
    return Post(**{**base, **kw})


def comment(cid, parent=None, body="x"):
    return Comment(id=cid, post_id="t3_a", parent_id=parent, depth=0, author="u", score=1, created=None,
                   permalink=None, body_html=body)


def test_threads_due_schedule():
    s = Store(":memory:")
    s.record_hot("NixOS", [post()], at="2026-10-23T00:00:00+00:00")
    due = lambda at: [r["id"] for r in s.threads_due([2, 12], 24, at=at)]
    assert due("2026-10-23T00:10:00+00:00") == ["t3_a"]
    s.record_thread(None, "t3_a", [], at="2026-10-23T00:10:00+00:00")
    assert due("2026-10-23T01:00:00+00:00") == []
    assert due("2026-10-23T02:10:00+00:00") == ["t3_a"]
    s.record_thread(None, "t3_a", [], at="2026-10-23T02:10:00+00:00")
    assert due("2026-10-23T06:00:00+00:00") == []
    s.record_hot("NixOS", [post()], at="2026-10-23T12:00:00+00:00")
    assert due("2026-10-23T12:10:00+00:00") == ["t3_a"]
    s.record_thread(None, "t3_a", [], at="2026-10-23T12:10:00+00:00")
    assert due("2026-10-24T12:00:00+00:00") == []  # schedule exhausted


def test_threads_due_skips_refetch_when_complete():
    s = Store(":memory:")
    s.record_hot("NixOS", [post(comment_count=1)], at="2026-10-23T00:00:00+00:00")
    s.record_thread(None, "t3_a", [comment("t1_a")], at="2026-10-23T00:10:00+00:00")
    assert s.threads_due([2, 12], 24, at="2026-10-23T03:00:00+00:00") == []
    s.record_hot("NixOS", [post(comment_count=2)], at="2026-10-23T03:00:00+00:00")
    assert [r["id"] for r in s.threads_due([2, 12], 24, at="2026-10-23T03:00:00+00:00")] == ["t3_a"]


def test_threads_due_ignores_posts_out_of_hot():
    s = Store(":memory:")
    s.record_hot("NixOS", [post()], at="2026-10-20T00:00:00+00:00")
    assert s.threads_due([2, 12], 24, at="2026-10-23T00:00:00+00:00") == []


def test_comment_upsert_keeps_parent_and_body():
    s = Store(":memory:")
    s.record_hot("NixOS", [post()])
    s.record_thread(post(body_html="<p>full</p>"), "t3_a", [comment("t1_a"), comment("t1_b", parent="t1_a")])
    s.record_thread(None, "t3_a", [comment("t1_b", parent=None, body=None)])
    row = s.db.execute("SELECT parent_id, body_html FROM comments WHERE id = 't1_b'").fetchone()
    assert tuple(row) == ("t1_a", "x")
    p = s.db.execute("SELECT body_html, thread_fetches FROM posts").fetchone()
    assert tuple(p) == ("<p>full</p>", 2)


def test_refetch_keeps_deleted_comment():
    s = Store(":memory:")
    s.record_thread(post(), "t3_a", [comment("t1_a", body="<p>hi</p>"), comment("t1_b")])
    gone = Comment(id="t1_a", post_id="t3_a", parent_id=None, depth=0, author="[deleted]", score=None,
                   created=None, permalink=None, body_html=None)
    s.record_thread(None, "t3_a", [gone, Comment(**{**vars(gone), "id": "t1_c"})])
    rows = s.db.execute("SELECT id, author, score, body_html FROM comments ORDER BY id").fetchall()
    assert [tuple(r) for r in rows] == [("t1_a", "u", 1, "<p>hi</p>"), ("t1_b", "u", 1, "x"),
                                        ("t1_c", "[deleted]", None, None)]


def test_listing_does_not_clear_body():
    s = Store(":memory:")
    s.record_thread(post(body_html="<p>full</p>"), "t3_a", [])
    s.record_hot("NixOS", [post(body_html=None, score=50)])
    assert tuple(s.db.execute("SELECT body_html, score FROM posts").fetchone()) == ("<p>full</p>", 50)


def test_prune_by_retention_keeps_pinned_and_recent():
    s = Store(":memory:")
    s.record_hot("NixOS", [post("t3_old"), post("t3_kept")], at="2026-09-01T00:00:00+00:00")
    s.record_thread(None, "t3_old", [comment("t1_o")], at="2026-09-01T00:10:00+00:00")
    s.db.execute("UPDATE comments SET post_id = 't3_old'")
    s.set_pinned("t3_kept", True)
    s.record_hot("NixOS", [post("t3_new")], at="2026-10-20T00:00:00+00:00")
    s.log_fetch("GET", "u", 200, 1, 1, None)
    s.db.execute("UPDATE fetch_log SET at = '2026-08-01T00:00:00+00:00'")
    out = s.prune(30, at="2026-10-23T00:00:00+00:00")
    assert out == {"posts": 1, "comments": 1}
    assert {r[0] for r in s.db.execute("SELECT id FROM posts")} == {"t3_kept", "t3_new"}
    assert s.db.execute("SELECT count(*) FROM fetch_log").fetchone()[0] == 0
    assert s.db.execute("SELECT count(DISTINCT taken_at) FROM hot_snapshots").fetchone()[0] == 1


def test_prune_zero_keeps_everything():
    s = Store(":memory:")
    s.record_hot("NixOS", [post("t3_old")], at="2020-01-01T00:00:00+00:00")
    assert s.prune(0, at="2026-10-23T00:00:00+00:00") == {"posts": 0, "comments": 0}
    assert s.db.execute("SELECT count(*) FROM posts").fetchone()[0] == 1


def test_migrates_old_schema(tmp_path):
    import sqlite3

    db = tmp_path / "a.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE posts (id TEXT PRIMARY KEY, subreddit TEXT NOT NULL, title TEXT NOT NULL, author TEXT,"
                " score INTEGER, upvote_ratio REAL, comment_count INTEGER, created TEXT, post_type TEXT,"
                " permalink TEXT NOT NULL, url TEXT, domain TEXT, flair TEXT, body_html TEXT, first_seen TEXT NOT NULL,"
                " last_seen_hot TEXT, thread_fetches INTEGER NOT NULL DEFAULT 0, first_thread_fetch TEXT,"
                " last_thread_fetch TEXT)")
    con.commit()
    con.close()
    s = Store(db)
    s.record_hot("NixOS", [post()])
    s.set_pinned("t3_a", True)
    assert s.db.execute("SELECT pinned FROM posts").fetchone()[0] == 1


def test_prune_counts_from_latest_activity():
    s = Store(":memory:")
    s.record_hot("NixOS", [post("t3_a")], at="2026-09-01T00:00:00+00:00")  # last in hot long ago...
    s.record_thread(None, "t3_a", [], at="2026-10-22T00:00:00+00:00")  # ...but fetched yesterday
    assert s.prune(30, at="2026-10-23T00:00:00+00:00")["posts"] == 0
