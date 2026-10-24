import gzip
from pathlib import Path

from archivvit import parse

FIXTURES = Path(__file__).parent / "fixtures"


def load(name):
    return gzip.open(FIXTURES / f"{name}.html.gz").read().decode()


def test_hot_first_page():
    listing = parse.parse_listing(load("hot"))
    assert len(listing.posts) == 3
    assert all(p.id.startswith("t3_") for p in listing.posts)
    assert all(p.subreddit == "NixOS" and p.title and p.permalink.startswith("/r/NixOS/comments/") for p in listing.posts)
    assert listing.next_url and "/svc/shreddit/community-more-posts/hot/" in listing.next_url
    assert "&amp;" not in listing.next_url


def test_more_posts_partial_skips_ads():
    listing = parse.parse_listing(load("more_posts"))
    assert len(listing.posts) == 24
    assert len({p.id for p in listing.posts}) == 24
    assert listing.next_url
    p = listing.posts[0]
    assert p.score is not None and p.comment_count is not None and p.created


def test_thread():
    frag = parse.parse_comments(load("thread"))
    assert frag.post and frag.post.id == "t3_1xcf0jh"
    assert frag.post.body_html
    assert len(frag.comments) == 24
    ids = {c.id for c in frag.comments}
    assert all(c.post_id == "t3_1xcf0jh" for c in frag.comments)
    top = [c for c in frag.comments if c.depth == 0]
    assert top and all(c.parent_id is None for c in top)
    replies = [c for c in frag.comments if c.depth > 0]
    assert replies and all(c.parent_id in ids for c in replies)
    assert all(c.body_html for c in frag.comments if c.author != "[deleted]")
    assert frag.more and all(m.cursor and "/svc/shreddit/more-comments/" in m.url for m in frag.more)


def test_comment_body_is_own_not_childs():
    frag = parse.parse_comments(load("thread"))
    by_id = {c.id: c for c in frag.comments}
    child = next(c for c in frag.comments if c.parent_id)
    assert by_id[child.parent_id].body_html != child.body_html


def test_more_comments_response():
    frag = parse.parse_comments(load("more_comments"))
    assert frag.post is None
    assert len(frag.comments) == 2
    assert all(c.parent_id for c in frag.comments)


def test_continue_thread_page():
    frag = parse.parse_comments(load("continue_thread"))
    assert len(frag.comments) == 4


def test_challenge_detection():
    assert not parse.is_challenge(load("hot"))
    page = '<html><script>e.elements.namedItem("solution").value=n</script><form></form></html>'
    assert parse.is_challenge(page)
