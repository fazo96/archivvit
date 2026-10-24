"""Build the test fixtures from real Reddit pages, keeping only what the parser reads.

    python tests/fixtures/anonymize.py <dir with real *.html.gz> tests/fixtures

Real pages are not committed. Every element the parser does not look at is dropped
(scripts, styles, sidebars, ads' content, user cards), and what is left is scrubbed:
authors become user1, user2..., titles, bodies and flair become lorem ipsum of about the
same length, URL slugs become lorem_ipsum, external links point to example.com.
Ids, scores, timestamps, cursors and the nesting are kept, as is the page layout the
parser depends on: thread comments are streamed in <template> blocks after </html>.
"""

import copy
import gzip
import re
import sys
from pathlib import Path

import lxml.html
from cssselect import HTMLTranslator
from lxml import etree

LOREM = (
    "lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod tempor incididunt ut labore "
    "et dolore magna aliqua ut enim ad minim veniam quis nostrud exercitation ullamco laboris nisi ut "
    "aliquip ex ea commodo consequat duis aute irure dolor in reprehenderit in voluptate velit esse"
).split()

KEEP_TAGS = {
    "shreddit-post", "shreddit-post-text-body", "shreddit-post-flair", "shreddit-comment",
    "shreddit-comment-tree", "faceplate-partial", "input", "a",
}
KEEP_ATTRS = {
    "shreddit-post": {"id", "subreddit-name", "subreddit-prefixed-name", "post-title", "author", "score",
                      "upvote-ratio", "comment-count", "created-timestamp", "post-type", "permalink",
                      "content-href", "domain", "view-context", "feedindex"},
    "shreddit-post-text-body": {"slot", "post-id"},
    "shreddit-post-flair": {"slot", "post-id"},
    "shreddit-comment": {"thingid", "parentid", "depth", "author", "score", "created", "permalink", "postid",
                         "content-type"},
    "shreddit-comment-tree": {"id", "post-id", "totalcomments", "permalink"},
    "faceplate-partial": {"class", "src", "method", "loading", "slot"},
    "input": {"type", "name", "value"},
    "a": {"href", "slot"},
    "div": {"id", "slot"},
}
REDDIT_HOSTS = ("reddit.com", "redd.it", "redditmedia.com", "redditstatic.com")


def lorem(n_chars: int, offset: int = 0) -> str:
    words, i = [], offset
    while sum(len(w) + 1 for w in words) < max(n_chars, 4):
        words.append(LOREM[i % len(LOREM)])
        i += 1
    s = " ".join(words)
    return s[0].upper() + s[1:]


class Scrubber:
    def __init__(self):
        self.users = {"[deleted]": "[deleted]"}
        self.n = 0

    def user(self, name):
        if name not in self.users:
            self.users[name] = f"user{len(self.users)}"
        return self.users[name]

    def text(self, s):
        self.n += 1
        return lorem(len(s.strip()), self.n) if s and s.strip() else s

    @staticmethod
    def url(v):
        v = re.sub(r"(/comments/[a-z0-9]+/)(?!comment/)[^/?\"#]+", r"\1lorem_ipsum", v)
        m = re.match(r"https?://([^/]+)", v)
        if m and not m.group(1).endswith(REDDIT_HOSTS):
            return "https://example.com/lorem-ipsum"
        return v

    def attrs(self, el):
        allowed = KEEP_ATTRS.get(el.tag, set())
        for k in list(el.attrib):
            if k not in allowed:
                del el.attrib[k]
        for k, v in el.attrib.items():
            if k == "author":
                el.set(k, self.user(v))
            elif k == "post-title":
                el.set(k, self.text(v))
            elif k in ("permalink", "content-href", "href", "src"):
                el.set(k, self.url(v))
            elif k == "domain" and not v.startswith("self.") and not v.endswith(REDDIT_HOSTS):
                el.set(k, "example.com")

    def body(self, div):
        """A post or comment body: one lorem paragraph per original paragraph."""
        paras = [p.text_content() for p in div.iter("p")] or [div.text_content()]
        for c in list(div):
            div.remove(c)
        div.text = None
        for t in paras:
            p = etree.SubElement(div, "p")
            p.text = self.text(t) or "Lorem."
        for k in list(div.attrib):
            if k not in KEEP_ATTRS["div"]:
                del div.attrib[k]

    def element(self, el):
        el = copy.deepcopy(el)
        el.tail = None
        drop = el.xpath(
            ".//script | .//style | .//svg | .//template | .//shreddit-comment-tree-ad | .//comment()"
            " | .//faceplate-partial[not(contains(@src, '/more-comments/') or contains(@src, '/community-more-posts/'))]"
        )
        for bad in drop:
            bad.getparent().remove(bad)
        is_body = lambda n: n.tag == "div" and "-rtjson-content" in n.get("id", "")
        bodies = [n for n in el.iter() if isinstance(n.tag, str) and is_body(n)]
        for b in bodies:
            self.body(b)
        # Live element proxies, so membership is stable (unlike id() of a temporary proxy).
        inside = {n for b in bodies for n in b.iter() if n is not b}
        flairs = {n for n in el.iter("shreddit-post-flair") if n.text_content().strip()}
        for node in el.iter():
            if node in inside:
                continue
            node.text = None
            if node.getparent() is None or node.getparent() not in inside:
                node.tail = None
            if node in flairs:
                for c in list(node):
                    node.remove(c)
                node.text = "Lorem"
            if node.tag == "a" and node.get("slot") == "more-comments-permalink":
                node.text = "More replies"
        keep = lambda n: (n.tag in KEEP_TAGS and (n.tag != "a" or n.get("slot") == "more-comments-permalink")) or is_body(n)
        for node in list(el.iter()):
            if node is el or node in inside:
                continue
            if keep(node):
                self.attrs(node)
            else:
                node.drop_tag()
        if el not in inside:
            self.attrs(el)
        return el


def select(doc, css):
    return doc.getroottree().xpath(HTMLTranslator().css_to_xpath(css, prefix="//"))


def outermost(nodes, tag):
    return [n for n in nodes if not any(a.tag == tag for a in n.iterancestors())]


def html(el) -> str:
    return etree.tostring(el, encoding="unicode", method="html")


def page(head: list, templates: list | None = None) -> str:
    out = "<!DOCTYPE html>\n<html lang=\"en\"><head><title>r/NixOS</title></head><body>\n"
    out += "\n".join(head) + "\n</body></html>\n"
    for i, t in enumerate(templates or []):
        out += f'<template for="s_0_{i}"><div>\n{t}\n</div></template>\n'
    return out


def main(src: Path, dst: Path):
    s = Scrubber()
    read = lambda name: lxml.html.document_fromstring(gzip.open(src / f"{name}.html.gz").read())

    for name in ("hot", "more_posts"):
        doc = read(name)
        parts = [html(s.element(p)) for p in select(doc, "shreddit-post")]
        parts += ["<shreddit-ad-post></shreddit-ad-post>"] * len(select(doc, "shreddit-ad-post"))
        nxt = [p for p in select(doc, "faceplate-partial[src]") if "community-more-posts" in p.get("src")]
        parts += [html(s.element(p)) for p in nxt[:1]]
        write(dst / f"{name}.html.gz", page(parts))

    for name in ("thread", "continue_thread"):
        doc = read(name)
        post = [p for p in select(doc, "shreddit-post") if p.get("view-context") == "CommentsPage"]
        # Reddit streams the tree over several <template> chunks; gather every top-level
        # comment and loader, wherever it sits, into one chunk.
        items = select(doc, "shreddit-comment, faceplate-partial.more-comments-partial, a[slot=more-comments-permalink]")
        items = [i for i in items if not any(a.tag == "shreddit-comment" for a in i.iterancestors())]
        tree = select(doc, "shreddit-comment-tree")[0]
        attrs = " ".join(f'{k}="{v}"' for k, v in tree.attrib.items() if k in KEEP_ATTRS["shreddit-comment-tree"])
        chunk = f"<shreddit-comment-tree {s.url(attrs)}>\n" + "\n".join(html(s.element(i)) for i in items) + "\n</shreddit-comment-tree>"
        write(dst / f"{name}.html.gz", page([html(s.element(p)) for p in post], [chunk]))

    doc = read("more_comments")
    comments = outermost(select(doc, "shreddit-comment"), "shreddit-comment")
    write(dst / "more_comments.html.gz", "\n".join(html(s.element(c)) for c in comments))
    print(f"{len(s.users) - 1} authors renamed")


def write(path: Path, text: str):
    with gzip.open(path, "wt", compresslevel=9) as f:
        f.write(text)
    print(f"{path.name}: {len(text)} chars")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
