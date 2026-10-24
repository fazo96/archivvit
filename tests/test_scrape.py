import gzip
from pathlib import Path

import pytest

from archivvit.scrape import fetch_hot

FIXTURES = Path(__file__).parent / "fixtures"


class FakeReddit:
    def __init__(self, pages):
        self.pages, self.urls = pages, []

    def get(self, url):
        self.urls.append(url)
        return self.pages[url]


def page(name):
    return gzip.open(FIXTURES / f"{name}.html.gz").read().decode()


def test_fetch_hot_follows_pagination():
    hot = page("hot")
    from archivvit.parse import parse_listing

    nxt = parse_listing(hot).next_url
    r = FakeReddit({"https://www.reddit.com/r/NixOS/hot/": hot, nxt: page("more_posts")})
    posts = fetch_hot(r, "NixOS", 25)
    assert len(posts) == 25 and len({p.id for p in posts}) == 25
    assert r.urls == ["https://www.reddit.com/r/NixOS/hot/", nxt]


def test_fetch_hot_fixes_capitalization():
    empty = "<html><body><a href='/r/NixOS/'>r/NixOS</a></body></html>"
    r = FakeReddit({"https://www.reddit.com/r/nixos/hot/": empty, "https://www.reddit.com/r/NixOS/hot/": page("hot")})
    assert len(fetch_hot(r, "nixos", 3)) == 3


def test_fetch_hot_empty_is_an_error():
    r = FakeReddit({"https://www.reddit.com/r/nope/hot/": "<html><body>nothing</body></html>"})
    with pytest.raises(RuntimeError, match="no posts"):
        fetch_hot(r, "nope", 25)
