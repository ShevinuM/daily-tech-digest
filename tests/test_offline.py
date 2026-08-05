#!/usr/bin/env python3
"""
Offline test suite. No network — every request is stubbed.

    python3 tests/test_offline.py

Covers utils, each feed module, feed auto-discovery, and the Telegraph
converter — where the bugs actually live. Does NOT cover live HTTP; run
`main.py fetch --verbose` and `main.py publish --dry-run` by hand after
changing anything that makes a request.
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import feeds  # noqa: E402
import publish as P  # noqa: E402
import utils  # noqa: E402
from feeds import dev_to, hacker_news, medium, pragmatic_engineer  # noqa: E402

FAILURES = []
COUNT = 0


def check(name, cond, extra=""):
    global COUNT
    COUNT += 1
    if cond:
        print(f"  pass  {name}")
    else:
        print(f"  FAIL  {name} :: {extra}")
        FAILURES.append(name)


def section(title):
    print(f"\n{title}")


NOW = datetime.now(timezone.utc)
FRESH = NOW - timedelta(hours=2)
STALE = NOW - timedelta(hours=40)
CUTOFF = NOW - timedelta(hours=24)


def rfc(d):
    return d.strftime("%a, %d %b %Y %H:%M:%S GMT")


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

MEDIUM_XML = f"""<rss><channel>
<item><title><![CDATA[Fresh Post]]></title>
<link>https://medium.com/@x/fresh-abc?source=rss----1</link>
<dc:creator><![CDATA[Ann]]></dc:creator><pubDate>{rfc(FRESH)}</pubDate>
<category><![CDATA[python]]></category><category><![CDATA[api]]></category>
<description><![CDATA[<p class="medium-feed-snippet">A <b>snippet</b> here.</p>]]></description></item>
<item><title><![CDATA[Stale Post]]></title><link>https://medium.com/@x/stale-def</link>
<pubDate>{rfc(STALE)}</pubDate><description><![CDATA[old]]></description></item>
</channel></rss>"""

PRAG_XML = f"""<rss><channel>
<item><title>Fresh PE</title><link>https://blog.pragmaticengineer.com/fresh/</link>
<dc:creator>Gergely</dc:creator><pubDate>{rfc(FRESH)}</pubDate>
<description>dek text</description>
<content:encoded><![CDATA[<p>Full <em>body</em> here.</p>]]></content:encoded></item>
<item><title>Stale PE</title><link>https://blog.pragmaticengineer.com/stale/</link>
<pubDate>{rfc(STALE)}</pubDate><description>old</description></item>
</channel></rss>"""

DEVTO = [
    {"title": "Fresh A", "url": "https://dev.to/a/fresh-a", "path": "/a/fresh-a",
     "published_at": FRESH.strftime("%Y-%m-%dT%H:%M:%SZ"), "user": {"username": "a"},
     "public_reactions_count": 50, "comments_count": 3, "reading_time_minutes": 5,
     "tag_list": ["react"], "description": "desc a"},
    {"title": "Fresh B", "url": "https://dev.to/b/fresh-b", "path": "/b/fresh-b",
     "published_at": FRESH.strftime("%Y-%m-%dT%H:%M:%SZ"), "user": {"username": "b"},
     "public_reactions_count": 90, "comments_count": 1, "reading_time_minutes": 4,
     "tag_list": ["node"], "description": "desc b"},
    {"title": "Stale C", "url": "https://dev.to/c/stale-c", "path": "/c/stale-c",
     "published_at": STALE.strftime("%Y-%m-%dT%H:%M:%SZ"), "user": {"username": "c"},
     "public_reactions_count": 999, "comments_count": 0,
     "tag_list": [], "description": "old"},
]

HN_IDS = [1, 2, 3]
HN = {
    1: {"type": "story", "id": 1, "title": "HN fresh high", "url": "https://ex.com/1",
        "time": int(FRESH.timestamp()), "score": 300, "by": "u1", "descendants": 80},
    2: {"type": "story", "id": 2, "title": "HN fresh low", "url": "https://ex.com/2",
        "time": int(FRESH.timestamp()), "score": 5, "by": "u2", "descendants": 1},
    3: {"type": "story", "id": 3, "title": "HN paywalled",
        "url": "https://www.wsj.com/x", "time": int(FRESH.timestamp()),
        "score": 200, "by": "u3", "descendants": 10},
}


def fake_get(url, as_json=False, timeout=None):
    if "medium.com/feed" in url:
        return MEDIUM_XML
    if "pragmaticengineer" in url:
        return PRAG_XML
    if "topstories" in url:
        return HN_IDS
    if "/v0/item/" in url:
        return HN[int(url.split("/item/")[1].split(".json")[0])]
    if "dev.to/api/articles/" in url:
        return {"body_markdown": "Line one.\n\n  Line   two."}
    if "dev.to/api/articles" in url:
        return DEVTO
    raise RuntimeError("unexpected url " + url)


# --------------------------------------------------------------------------

def test_utils():
    section("utils / dates")
    check("parse_rfc822 returns tz-aware",
          utils.parse_rfc822(rfc(FRESH)).tzinfo is not None)
    check("parse_rfc822 rejects garbage", utils.parse_rfc822("garbage") is None)
    check("parse_rfc822 handles empty", utils.parse_rfc822("") is None)
    check("parse_iso round-trips", utils.parse_iso(utils.iso(FRESH)) is not None)
    check("parse_iso rejects garbage", utils.parse_iso("nope") is None)
    check("rss_date on missing field", utils.rss_date("<item>x</item>") is None)

    section("utils / html")
    check("strip_html empty input",
          utils.strip_html("", 50) == "" and utils.strip_html(None, 50) == "")
    check("strip_html collapses nested tags",
          utils.strip_html("<div><p>a</p>\n<p>b</p></div>", 50) == "a b",
          repr(utils.strip_html("<div><p>a</p>\n<p>b</p></div>", 50)))
    check("strip_html truncates",
          utils.strip_html("<b>" + "x" * 100 + "</b>", 10) == "x" * 10)
    check("clean_url strips ?source=",
          utils.clean_url("https://a.com/b?source=rss--1") == "https://a.com/b")

    section("utils / item + paywall")
    check("paywall hint matches medium not dev.to",
          utils.is_paywalled("https://medium.com/x")
          and not utils.is_paywalled("https://dev.to/x"))
    it = utils.item(source="s", title=" T ", url="https://wsj.com/a?source=x",
                    published_at="2026-01-01T00:00:00Z")
    check("item strips tracking + trims title",
          it["url"] == "https://wsj.com/a" and it["title"] == "T", it)
    check("item auto-detects paywall", it["paywalled"] is True)
    check("item has every required field",
          all(k in it for k in utils.ITEM_FIELDS))
    it2 = utils.item(source="s", title="T", url="https://dev.to/a",
                     published_at="x", paywalled=False, score=9)
    check("item keeps source-specific extras", it2["score"] == 9)

    section("utils / parallel")
    results = list(utils.parallel(lambda x: x * 2, [1, 2, 3]))
    check("parallel returns all jobs", len(results) == 3, len(results))
    check("parallel results correct",
          sorted(r for _, r, _ in results) == [2, 4, 6])
    bad = list(utils.parallel(lambda x: 1 / 0, [1]))
    check("parallel captures errors instead of raising", bad[0][2] is not None)


def test_discovery():
    section("feeds / auto-discovery")
    mods = feeds.discover()
    names = [m.NAME for m in mods]
    check("finds all four feed modules", len(mods) == 4, names)
    check("expected names present",
          set(names) == {"dev_to", "medium", "pragmatic_engineer", "hacker_news"},
          names)
    check("every feed exposes callable fetch",
          all(callable(m.fetch) for m in mods))
    check("--only filter works",
          [m.NAME for m in feeds.discover(only=["dev_to"])] == ["dev_to"])
    check("unknown --only returns nothing", feeds.discover(only=["nope"]) == [])
    check("names() helper matches discover()", set(feeds.names()) == set(names))

    # A module with ENABLED = False must be skipped without deleting the file.
    medium.ENABLED = False
    try:
        check("ENABLED = False excludes a feed",
              "medium" not in [m.NAME for m in feeds.discover()])
    finally:
        medium.ENABLED = True
    check("re-enabling restores it", "medium" in [m.NAME for m in feeds.discover()])


def test_feeds():
    utils.http_get = fake_get  # every feed module calls through utils

    section("feeds / dev_to")
    d, e = dev_to.fetch(CUTOFF, verbose=False, want_bodies=True)
    check("drops items older than cutoff",
          all("stale" not in i["url"] for i in d), [i["url"] for i in d])
    check("dedupes across overlapping feeds", len(d) == 2, len(d))
    check("ranks by reaction count", d[0]["title"] == "Fresh B", d[0]["title"])
    check("body excerpt whitespace collapsed",
          d[0]["body_excerpt"] == "Line one. Line two.", repr(d[0]["body_excerpt"]))
    check("not flagged paywalled", all(not i["paywalled"] for i in d))
    check("no errors on happy path", e == [], e)
    d2, _ = dev_to.fetch(CUTOFF, verbose=False, want_bodies=False)
    check("--no-bodies skips body fetch", all(i["body_excerpt"] is None for i in d2))

    section("feeds / medium")
    m, _ = medium.fetch(CUTOFF, verbose=False)
    check("drops stale", len(m) == 1, len(m))
    check("strips ?source= tracking param",
          m[0]["url"] == "https://medium.com/@x/fresh-abc", m[0]["url"])
    check("every item flagged paywalled", m[0]["paywalled"] is True)
    check("carries a paywall note", "paywall_note" in m[0])
    check("snippet html stripped cleanly",
          m[0]["description"] == "A snippet here.", repr(m[0]["description"]))
    check("categories parsed", m[0]["tags"] == ["python", "api"], m[0]["tags"])

    section("feeds / pragmatic_engineer")
    p, _ = pragmatic_engineer.fetch(CUTOFF, verbose=False)
    check("drops stale", len(p) == 1, len(p))
    check("full body from content:encoded",
          p[0]["body_excerpt"] == "Full body here.", repr(p[0]["body_excerpt"]))
    check("not flagged paywalled", p[0]["paywalled"] is False)

    section("feeds / hacker_news")
    h, _ = hacker_news.fetch(CUTOFF, verbose=False)
    check("score floor applied",
          all(i["score"] >= hacker_news.MIN_SCORE for i in h), [i["score"] for i in h])
    check("kept 2 of 3 stories", len(h) == 2, len(h))
    check("paywall domain flagged",
          any(i["paywalled"] for i in h if "wsj" in i["url"]))
    check("sorted by score desc", h[0]["score"] == 300, h[0]["score"])
    check("discussion url always present",
          all(i["discussion_url"].startswith("https://news.ycombinator.com")
              for i in h))

    section("feeds / shared contract")
    for name, items in (("dev_to", d), ("medium", m),
                        ("pragmatic_engineer", p), ("hacker_news", h)):
        check(f"{name} returns the normalised item shape",
              all(all(k in i for k in utils.ITEM_FIELDS) for i in items))


def walk(nodes):
    for n in nodes:
        if isinstance(n, dict):
            yield n
            yield from walk(n.get("children") or [])


def test_publish():
    section("publish / entity decoding")
    n, _ = P.html_to_nodes("<p>AI &amp; LLMs &mdash; &ldquo;quoted&rdquo; &#128295;</p>")
    txt = "".join(c for c in n[0]["children"] if isinstance(c, str))
    check("entities decode to real characters",
          txt == "AI & LLMs — “quoted” \U0001f527", repr(txt))

    section("publish / tag handling")
    n, _ = P.html_to_nodes("<h1>A</h1><h2>B</h2><h5>C</h5><div>D</div>")
    check("h1 and h2 remap to h3",
          [x["tag"] for x in n[:2]] == ["h3", "h3"], [x["tag"] for x in n])
    check("h5 remaps to h4", n[2]["tag"] == "h4", n[2]["tag"])
    check("div remaps to p", n[3]["tag"] == "p", n[3]["tag"])
    n, _ = P.html_to_nodes("<p>keep <span>this</span> text</p>")
    joined = "".join(c for c in n[0]["children"] if isinstance(c, str)).strip()
    check("span unwrapped, inner text kept", joined == "keep this text",
          n[0]["children"])
    n, _ = P.html_to_nodes("<p>safe</p><script>alert(1)</script><style>b{}</style>")
    check("script and style dropped entirely",
          len(n) == 1 and "alert" not in json.dumps(n), json.dumps(n))
    n, _ = P.html_to_nodes("<p>a<br>b</p><hr>")
    check("void tags not pushed onto the stack",
          [x["tag"] for x in n] == ["p", "hr"], [x["tag"] for x in n])

    section("publish / attributes")
    n, _ = P.html_to_nodes('<p><a href="https://x.com" onclick="evil()" class="c">l</a></p>')
    a = [x for x in walk(n) if x.get("tag") == "a"][0]
    check("href preserved", a["attrs"] == {"href": "https://x.com"}, a.get("attrs"))
    check("onclick and class stripped",
          "onclick" not in json.dumps(n) and "class" not in json.dumps(n))

    section("publish / structure")
    n, _ = P.html_to_nodes("<ul><li>one</li><li>two <b>bold</b></li></ul>")
    check("list nesting preserved",
          n[0]["tag"] == "ul" and len(n[0]["children"]) == 2, json.dumps(n))
    check("nested inline tag inside li", any(x.get("tag") == "b" for x in walk(n)))
    n, _ = P.html_to_nodes("bare text at top level")
    check("bare top-level text wrapped in p", n[0]["tag"] == "p", n)
    n, _ = P.html_to_nodes("<p>unclosed <b>bold</p>")
    check("malformed html does not crash", isinstance(n, list) and len(n) > 0)
    n, _ = P.html_to_nodes("<p>x</p>")
    check("only telegraph-legal tags emitted",
          all(x["tag"] in P.ALLOWED for x in walk(n)))

    section("publish / guards")
    big = "<p>" + ("word " * 30000) + "</p>"
    n, _ = P.html_to_nodes(big)
    check("oversize content detected",
          len(json.dumps(n, ensure_ascii=False).encode()) > P.MAX_CONTENT_BYTES)
    check("missing html file returns exit 3",
          P.run(html_path="/nonexistent/x.html", title="t") == 3)
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as f:
        f.write("<script>only junk</script>")
        junk = f.name
    check("empty converted content returns exit 3",
          P.run(html_path=junk, title="t") == 3)
    os.unlink(junk)


def main():
    test_utils()
    test_discovery()
    test_feeds()
    test_publish()
    print(f"\n{COUNT - len(FAILURES)}/{COUNT} passed")
    if FAILURES:
        print("FAILED: " + ", ".join(FAILURES))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
