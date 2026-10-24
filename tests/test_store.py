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


def test_listing_does_not_clear_body():
    s = Store(":memory:")
    s.record_thread(post(body_html="<p>full</p>"), "t3_a", [])
    s.record_hot("NixOS", [post(body_html=None, score=50)])
    assert tuple(s.db.execute("SELECT body_html, score FROM posts").fetchone()) == ("<p>full</p>", 50)
