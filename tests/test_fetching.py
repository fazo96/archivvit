"""Reddit URL handling and on-demand fetching in the web UI, the fetcher, and the client's turns."""

import json
import threading
import time

import pytest

from archivvit import config
from archivvit.client import Reddit
from archivvit.jobs import Fetcher, Job
from archivvit.store import Store
from archivvit.web import create_app, focus_comment
from test_web import comment, post


class FakeFetcher:
    """Records submissions; jobs can be given a state to show."""

    def __init__(self):
        self.submitted, self.jobs, self.reddit = [], {}, self

    def submit(self, kind, target):
        self.submitted.append((kind, target))
        job = self.jobs[(kind, target)] = Job(kind, target, state="queued")
        return job

    def status(self, kind, target):
        return self.jobs.get((kind, target))

    def location(self, url):  # the client's share-link resolution
        return "https://www.reddit.com/r/NixOS/comments/aaa/post/?share_id=x&utm_source=share"


@pytest.fixture
def app_and_fetcher(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps({"subreddits": ["NixOS"]}))
    s = Store(config.load(tmp_path).db_path)
    s.record_hot("NixOS", [post("t3_aaa")])
    s.record_thread(None, "t3_aaa", [comment("t1_a", body="<p>first</p>"), comment("t1_b", parent="t1_a")])
    s.close()
    f = FakeFetcher()
    app = create_app(tmp_path, fetcher=f)
    return app.test_client(), f, tmp_path


def test_focus_comment_url_shapes():
    assert focus_comment("comment/abc123/") == "abc123"
    assert focus_comment("some_title/abc123/") == "abc123"
    assert focus_comment("some_title/") is None
    assert focus_comment(None) is None


def test_comment_link_highlights_archived_comment(app_and_fetcher):
    client, _, _ = app_and_fetcher
    html = client.get("/r/NixOS/comments/aaa/comment/a/").get_data(as_text=True)
    assert 'class="comment focus" id="t1_a"' in html
    assert 'getElementById("t1_a").scrollIntoView' in html
    assert "isn't in the archive" not in html


def test_missing_comment_offers_fetch(app_and_fetcher):
    client, _, _ = app_and_fetcher
    html = client.get("/r/NixOS/comments/aaa/post/zzz/").get_data(as_text=True)  # old-style comment URL
    assert "linked comment isn't in the archive" in html
    assert 'name="target" value="aaa/zzz"' in html


def test_fetch_queues_job_and_page_reloads(app_and_fetcher):
    client, f, _ = app_and_fetcher
    r = client.post("/fetch", data={"kind": "thread", "target": "zzz", "next": "/r/NixOS/comments/zzz/"})
    assert r.status_code == 302 and r.headers["Location"] == "/r/NixOS/comments/zzz/"
    assert f.submitted == [("thread", "zzz")]
    html = client.get("/r/NixOS/comments/zzz/").get_data(as_text=True)
    assert "Fetching from Reddit" in html and 'http-equiv="refresh"' in html


def test_fetch_rejects_bad_input_and_foreign_origin(app_and_fetcher):
    client, f, _ = app_and_fetcher
    assert client.post("/fetch", data={"kind": "thread", "target": "../x"}).status_code == 400
    assert client.post("/fetch", data={"kind": "nope", "target": "abc"}).status_code == 400
    r = client.post("/fetch", data={"kind": "thread", "target": "abc", "next": "//evil.example/"})
    assert r.headers["Location"] == "/"
    r = client.post("/fetch", data={"kind": "thread", "target": "abc"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    r = client.post("/fetch", data={"kind": "thread", "target": "abc"}, headers={"Origin": "http://localhost"})
    assert r.status_code == 302


def test_failed_job_shows_error(app_and_fetcher):
    client, f, _ = app_and_fetcher
    f.jobs[("thread", "zzz")] = Job("thread", "zzz", state="failed", error="Blocked: 403")
    html = client.get("/r/NixOS/comments/zzz/").get_data(as_text=True)
    assert "The fetch failed: Blocked: 403" in html and "Fetch from Reddit" in html
    assert 'http-equiv="refresh"' not in html


def test_short_and_gallery_links(app_and_fetcher):
    client, _, _ = app_and_fetcher
    assert client.get("/comments/aaa/").headers["Location"] == "/r/NixOS/comments/aaa/"
    assert client.get("/comments/aaa/x/comment/b/").headers["Location"] == "/r/NixOS/comments/aaa/x/comment/b/"
    assert "isn't in the archive" in client.get("/comments/zzz/").get_data(as_text=True)
    assert client.get("/gallery/aaa").headers["Location"].endswith("/comments/aaa/")


def test_share_link_resolves_to_local_path(app_and_fetcher):
    client, _, _ = app_and_fetcher
    r = client.get("/r/NixOS/s/AbC123")
    assert r.status_code == 302 and r.headers["Location"] == "/r/NixOS/comments/aaa/post/"


def test_sort_suffix_redirects_and_unsupported_pages(app_and_fetcher):
    client, _, _ = app_and_fetcher
    assert client.get("/r/NixOS/new/").headers["Location"] == "/r/NixOS/"
    r = client.get("/user/someone/")
    assert r.status_code == 404 and "Not available here" in r.get_data(as_text=True)


def test_add_subreddit(app_and_fetcher):
    client, _, tmp_path = app_and_fetcher
    assert client.post("/subreddits/add", data={"sub": "linux"}).status_code == 302
    client.post("/subreddits/add", data={"sub": "LINUX"})
    assert json.loads((tmp_path / "settings.json").read_text())["subreddits"] == ["NixOS", "linux"]


class PageReddit:
    """A client stand-in serving fixture pages by URL."""

    def __init__(self, pages):
        self.pages = pages

    def urgent(self):
        return self

    def get(self, url):
        return self.pages.get(url, "")

    def post(self, url, data):
        return ""


def test_fetcher_thread_job_saves_first_page_then_counts(tmp_path):
    import gzip
    from pathlib import Path

    thread = gzip.open(Path(__file__).parent / "fixtures/thread.html.gz").read().decode()
    f = Fetcher(tmp_path, PageReddit({"https://www.reddit.com/comments/1xcf0jh/": thread}))
    job = f.submit("thread", "1xcf0jh")
    f.run_job(f.queue.get())
    assert job.state == "done", job.error
    s = Store(config.load(tmp_path).db_path)
    p = s.post("t3_1xcf0jh")
    assert p["archived_comments"] == 24 and p["thread_fetches"] == 1 and p["last_seen_hot"] is None
    # not in hot, so never scheduled for re-fetching
    assert s.threads_due([2, 12], 24) == []


def test_fetcher_dedupes_active_jobs_and_reports_failures(tmp_path):
    f = Fetcher(tmp_path, PageReddit({}))
    a, b = f.submit("thread", "abc"), f.submit("thread", "ABC")
    assert a is b and f.queue.qsize() == 1
    f.run_job(f.queue.get())
    assert a.state == "failed" and "no post" in a.error
    assert f.submit("thread", "abc") is not a  # finished jobs can be retried


def test_hot_fetch_of_other_subreddit_schedules_no_threads(tmp_path):
    s = Store(":memory:")
    s.record_hot("other", [post("t3_x", subreddit="other")])
    s.record_hot("NixOS", [post("t3_y")])
    assert [r["id"] for r in s.threads_due([2], 24, subreddits=["nixos"])] == ["t3_y"]


def test_client_serves_urgent_requests_first(tmp_path):
    cfg = config.load(tmp_path)
    r = Reddit(cfg, None)
    order, gate = [], threading.Event()

    def fake(method, url, data, follow):
        if url == "first":
            gate.wait(5)
        order.append(url)
        return ""

    r._request = fake
    threads = [threading.Thread(target=r.request, args=("GET", "first"))]
    threads[0].start()
    time.sleep(0.1)  # "first" holds the turn
    for name, urgent in [("normal", False), ("urgent", True)]:
        t = threading.Thread(target=r.request, args=("GET", name), kwargs={"urgent": urgent})
        t.start()
        threads.append(t)
        time.sleep(0.1)
    gate.set()
    for t in threads:
        t.join(5)
    assert order == ["first", "urgent", "normal"]
