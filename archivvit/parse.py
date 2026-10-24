"""Turn new-Reddit ("shreddit") server HTML into posts and comments.

lxml is used on purpose: Reddit streams the comment tree inside
`<template for="...">` blocks, which HTML5 parsers (lexbor, html5lib) keep out
of the document tree. libxml2 parses straight through them.
"""

import re
from dataclasses import dataclass, field
from html import unescape
from urllib.parse import urljoin

import lxml.html
from cssselect import HTMLTranslator
from lxml import etree

BASE = "https://www.reddit.com"


@dataclass
class Post:
    id: str
    subreddit: str
    title: str
    author: str | None
    score: int | None
    upvote_ratio: float | None
    comment_count: int | None
    created: str | None
    post_type: str | None
    permalink: str
    url: str | None
    domain: str | None
    flair: str | None = None
    body_html: str | None = None


@dataclass
class Comment:
    id: str
    post_id: str
    parent_id: str | None
    depth: int
    author: str | None
    score: int | None
    created: str | None
    permalink: str | None
    body_html: str | None


@dataclass
class MoreReplies:
    """A "N more replies" control: POST `url` with this cursor and the csrf token."""

    url: str
    cursor: str


@dataclass
class Fragment:
    """Comments found in a thread page, continue-thread page or more-comments response."""

    post: Post | None = None
    comments: list[Comment] = field(default_factory=list)
    more: list[MoreReplies] = field(default_factory=list)
    continue_urls: list[str] = field(default_factory=list)


@dataclass
class Listing:
    posts: list[Post]
    next_url: str | None


def _doc(html: str):
    if not html.strip():
        return lxml.html.fromstring("<html></html>")
    return lxml.html.document_fromstring(html)


_XPATH = {}


def _select(doc, css: str):
    """Match against the whole tree: libxml2 can leave content outside the first <html> node."""
    if css not in _XPATH:
        _XPATH[css] = etree.XPath(HTMLTranslator().css_to_xpath(css, prefix="//"))
    return _XPATH[css](doc.getroottree())


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _inner_html(el) -> str:
    return (el.text or "") + "".join(etree.tostring(c, encoding="unicode", method="html") for c in el)


def _abs(href: str) -> str:
    return urljoin(BASE, unescape(href))


def _post(el) -> Post:
    a = el.attrib
    flair = el.cssselect("shreddit-post-flair")
    flair_text = " ".join(flair[0].text_content().split()) if flair else ""
    body = el.cssselect(f"#{a['id']}-post-rtjson-content")
    return Post(
        id=a["id"],
        subreddit=a.get("subreddit-name", ""),
        title=a.get("post-title", ""),
        author=a.get("author"),
        score=_int(a.get("score")),
        upvote_ratio=_float(a.get("upvote-ratio")),
        comment_count=_int(a.get("comment-count")),
        created=a.get("created-timestamp"),
        post_type=a.get("post-type"),
        permalink=a.get("permalink", ""),
        url=a.get("content-href"),
        domain=a.get("domain"),
        flair=flair_text or None,
        body_html=_inner_html(body[0]).strip() if body else None,
    )


def parse_listing(html: str, sort: str = "hot") -> Listing:
    """A subreddit page or a community-more-posts partial. Ads are separate elements and skipped."""
    doc = _doc(html)
    posts = [_post(el) for el in _select(doc, "shreddit-post") if el.get("id", "").startswith("t3_")]
    nxt = None
    for el in _select(doc, "faceplate-partial[src]"):
        if f"/svc/shreddit/community-more-posts/{sort}/" in el.get("src"):
            nxt = _abs(el.get("src"))
            break
    return Listing(posts=posts, next_url=nxt)


def parse_comments(html: str) -> Fragment:
    doc = _doc(html)
    frag = Fragment()
    main = [el for el in _select(doc, "shreddit-post") if el.get("view-context") == "CommentsPage"]
    if main:
        frag.post = _post(main[0])
    for el in _select(doc, "shreddit-comment"):
        a = el.attrib
        cid = a.get("thingid")
        if not cid:
            continue
        body = el.cssselect(f"#{cid}-comment-rtjson-content")
        frag.comments.append(
            Comment(
                id=cid,
                post_id=a.get("postid", ""),
                parent_id=a.get("parentid") or None,
                depth=_int(a.get("depth")) or 0,
                author=a.get("author"),
                score=_int(a.get("score")),
                created=a.get("created"),
                permalink=a.get("permalink"),
                body_html=_inner_html(body[0]).strip() if body else None,
            )
        )
    for el in _select(doc, "faceplate-partial.more-comments-partial[src]"):
        cursor = el.cssselect("input[name=cursor]")
        if cursor and cursor[0].get("value"):
            frag.more.append(MoreReplies(url=_abs(el.get("src")), cursor=cursor[0].get("value")))
    seen = set()
    for el in _select(doc, "a[slot=more-comments-permalink][href]"):
        url = _abs(el.get("href"))
        if url not in seen:
            seen.add(url)
            frag.continue_urls.append(url)
    return frag


def spelled_as(html: str, sub: str) -> str | None:
    """Subreddit URLs are case-sensitive: /r/localllama/ is an empty page that still
    mentions r/LocalLLaMA. Returns that spelling, if the page has a different one."""
    for name in re.findall(r"\br/([A-Za-z0-9_]{2,21})\b", html):
        if name.lower() == sub.lower() and name != sub:
            return name
    return None


def is_challenge(html: str) -> bool:
    """The bot-check page served instead of content (small, auto-submits a `solution` form)."""
    return len(html) < 30_000 and 'namedItem("solution")' in html
