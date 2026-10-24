import json

import pytest

from archivvit import config
from archivvit.parse import Comment, Post
from archivvit.store import Store
from archivvit.web import Renderer, create_app


def post(pid, **kw):
    base = dict(id=pid, subreddit="NixOS", title=f"Post {pid}", author="alice", score=10, upvote_ratio=0.95,
                comment_count=2, created="2026-10-22T10:49:35.188000+0000", post_type="text",
                permalink=f"/r/NixOS/comments/{pid[3:]}/post/", url=None, domain="self.NixOS", flair="Help")
    return Post(**{**base, **kw})


def comment(cid, parent=None, score=1, body="<p>hi</p>"):
    return Comment(id=cid, post_id="t3_aaa", parent_id=parent, depth=0, author="bob", score=score,
                   created="2026-10-22T11:00:00.000000+0000", permalink=None, body_html=body)


@pytest.fixture
def client(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps({"subreddits": ["NixOS", "linux", "empty"]}))
    s = Store(config.load(tmp_path).db_path)
    s.record_hot("NixOS", [post("t3_aaa"), post("t3_bbb", title="Second <b>post</b>")])
    s.record_hot("linux", [post("t3_lll", subreddit="linux", title="Linux first", score=9000,
                                permalink="/r/linux/comments/lll/post/")])
    s.record_thread(
        post("t3_aaa", body_html='<p>see <a href="/r/NixOS/comments/bbb/x/">this</a> and '
                                 '<a href="/u/carol">carol</a><script>alert(1)</script></p>'),
        "t3_aaa",
        [comment("t1_a", score=5, body="<p>five</p>"), comment("t1_b", parent="t1_a", body="<p>reply</p>"),
         comment("t1_c", score=9, body="<p>nine</p>")],
    )
    s.close()
    app = create_app(tmp_path)
    app.config["TESTING"] = True
    return app.test_client()


def test_home_interleaves_by_rank(client):
    html = client.get("/").get_data(as_text=True)
    # NixOS #1, linux #1, NixOS #2: rank decides, not score; the empty subreddit is skipped
    assert html.index("Post t3_aaa") < html.index("Linux first") < html.index("Second")
    assert "Hot from 2 subreddits" in html
    assert 'href="/r/linux/">r/linux</a>' in html
    assert 'href="/" aria-current="page">Home' in html


def test_home_paging(client, monkeypatch):
    monkeypatch.setattr("archivvit.web.HOME_PAGE_SIZE", 2)
    first = client.get("/").get_data(as_text=True)
    assert "Second" not in first and "?page=2" in first
    second = client.get("/?page=2").get_data(as_text=True)
    assert "Second" in second and "Post t3_aaa" not in second and "← Previous" in second


def test_home_empty(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps({"subreddits": ["NixOS"]}))
    client = create_app(tmp_path).test_client()
    assert "Nothing archived yet" in client.get("/").get_data(as_text=True)


def test_subreddit_page(client):
    html = client.get("/r/NixOS/").get_data(as_text=True)
    assert "Post t3_aaa" in html and "Second &lt;b&gt;post&lt;/b&gt;" in html
    assert html.index("Post t3_aaa") < html.index("Second")
    assert "/r/NixOS/comments/aaa/" in html


def test_unknown_subreddit_404(client):
    assert client.get("/r/nope/").status_code == 404


def test_thread_page(client):
    html = client.get("/r/NixOS/comments/aaa/post/").get_data(as_text=True)
    assert "alert(1)" not in html
    assert 'href="/r/NixOS/comments/bbb/"' in html  # archived thread → local
    assert 'href="https://www.reddit.com/u/carol"' in html  # other relative links → Reddit
    # top sort: score 9 before score 5, the reply right under its parent
    assert html.index("<p>nine</p>") < html.index("<p>five</p>") < html.index("<p>reply</p>")


def test_thread_sort_old(client):
    html = client.get("/r/NixOS/comments/aaa/?sort=old").get_data(as_text=True)
    assert html.index("five") < html.index("nine")  # same created time: stable, insertion order


def test_missing_thread_404(client):
    assert client.get("/r/NixOS/comments/zzz/").status_code == 404


def test_settings_get_and_save(client, tmp_path):
    assert "Run now" not in client.get("/settings").get_data(as_text=True)  # no scheduler in tests
    r = client.post("/settings", data={
        "subreddits": "NixOS\nlinux", "posts_per_sub": "10", "scrape_interval_minutes": "30",
        "refetch_hours": "3", "hot_window_hours": "12", "max_thread_requests": "20",
        "min_delay": "2", "max_delay": "4", "retention_days": "0",
    })
    assert r.status_code == 302
    assert json.loads((tmp_path / "settings.json").read_text())["subreddits"] == ["NixOS", "linux"]
    assert "r/linux" in client.get("/settings").get_data(as_text=True)


def test_settings_errors_keep_input(client, tmp_path):
    r = client.post("/settings", data={"subreddits": "NixOS\nbad name!", "posts_per_sub": "10",
                                       "scrape_interval_minutes": "30", "refetch_hours": "3",
                                       "hot_window_hours": "12", "max_thread_requests": "20",
                                       "min_delay": "2", "max_delay": "4", "retention_days": "0"})
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "is not a subreddit name" in html and "bad name!" in html
    assert json.loads((tmp_path / "settings.json").read_text())["subreddits"] == ["NixOS", "linux", "empty"]


def test_renderer_linked_ids():
    assert Renderer.linked_ids('<a href="https://www.reddit.com/r/x/comments/abc/t/">', None) == ["t3_abc"]


def test_copy_links_are_reddit_urls(client):
    thread = client.get("/r/NixOS/comments/aaa/post/").get_data(as_text=True)
    assert 'class="copy-link" href="https://www.reddit.com/r/NixOS/comments/aaa/post/"' in thread
    home = client.get("/").get_data(as_text=True)
    assert 'class="copy-link" href="https://www.reddit.com/r/linux/comments/lll/post/"' in home


def test_keep_toggle(client):
    r = client.post("/r/NixOS/comments/aaa/keep", data={"keep": "1"})
    assert r.status_code == 302
    html = client.get("/r/NixOS/comments/aaa/").get_data(as_text=True)
    assert "Kept: never deleted" in html
    client.post("/r/NixOS/comments/aaa/keep", data={"keep": "0"})
    assert "Keep forever" in client.get("/r/NixOS/comments/aaa/").get_data(as_text=True)
    assert client.post("/r/NixOS/comments/zzz/keep", data={"keep": "1"}).status_code == 404
