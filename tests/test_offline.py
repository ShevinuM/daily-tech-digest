#!/usr/bin/env python3
"""
Offline test suite. No network — every request is stubbed.

    python3 tests/test_offline.py

Covers utils, each feed module, feed auto-discovery, newsletter
classification/unsubscribe, rank merge/prompt/llm_client, and the
reading-pace target-count calibration — where the bugs actually live. Does
NOT cover live HTTP; run `main.py fetch --verbose` and
`main.py digest --dry-run` by hand after changing anything that makes a
request.
"""

import contextlib
import io
import json
import math
import os
import socket
import sys
import time
import types
import urllib.error as _urllib_error
import urllib.request as _urllib_request
from datetime import datetime, timedelta, timezone

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import feeds  # noqa: E402
import main as M  # noqa: E402
import newsletters  # noqa: E402
import utils  # noqa: E402
from feeds import dev_to, hacker_news, medium, pragmatic_engineer  # noqa: E402
from newsletters import agentmail_client  # noqa: E402
from newsletters import classify  # noqa: E402
from newsletters import unsubscribe as unsub  # noqa: E402
from rank import enrich as rank_enrich  # noqa: E402
from rank import llm_client  # noqa: E402
from rank import merge as rank_merge  # noqa: E402
from rank import pools as rank_pools  # noqa: E402
from rank import prompt as rank_prompt  # noqa: E402
from rank import relevance as rank_relevance  # noqa: E402

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
    d, e = dev_to.fetch(CUTOFF, verbose=False)
    check("drops items older than cutoff",
          all("stale" not in i["url"] for i in d), [i["url"] for i in d])
    check("dedupes across overlapping feeds", len(d) == 2, len(d))
    check("ranks by reaction count", d[0]["title"] == "Fresh B", d[0]["title"])
    check("not flagged paywalled", all(not i["paywalled"] for i in d))
    check("no errors on happy path", e == [], e)
    check("fetch() no longer pre-fetches bodies (moved to rank/enrich.py)",
          all(i["body_excerpt"] is None for i in d))

    section("feeds / dev_to - fetch_body hook")
    body = dev_to.fetch_body(d[0])
    check("fetch_body returns raw body_markdown, newlines intact "
          "(rank/enrich.clean_for_summary needs real line starts to strip markdown)",
          body == "Line one.\n\n  Line   two.", repr(body))
    check("fetch_body returns None without a path", dev_to.fetch_body({}) is None)

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


def test_newsletters():
    section("newsletters / classify - non-content")
    check("welcome email flagged non-content",
          classify.is_non_content("Welcome to TLDR!", "Thanks for signing up."))
    check("short bodiless email flagged non-content",
          classify.is_non_content("hi", "ok"))
    check("real newsletter issue not flagged",
          not classify.is_non_content("TLDR AI 2026-08-05",
                                       "Here's today's roundup: " + "x" * 200 + " http://a.com"))

    section("newsletters / classify - date verification")
    check("date_from_url extracts /YYYY/MM/DD/",
          classify.date_from_url("https://blog.x.com/2026/08/03/post").date().isoformat()
          == "2026-08-03")
    check("date_from_url extracts YYYY-MM-DD dashes",
          classify.date_from_url("https://x.com/2026-08-03-post") is not None)
    check("date_from_url returns None without a date",
          classify.date_from_url("https://x.com/post") is None)
    known_dt = classify.date_from_snowflake("https://x.com/user/status/20")
    check("date_from_snowflake decodes the epoch-relative timestamp",
          known_dt is not None and known_dt.year == 2010, known_dt)
    check("verify_published_at finds a URL date",
          classify.verify_published_at("https://blog.x.com/2026/08/03/post") is not None)
    check("verify_published_at returns None when nothing is datable",
          classify.verify_published_at("https://example.com/post") is None)

    section("newsletters / classify - link extraction")
    html = ('<a href="https://a.com/2026-08-05-real-post">Real Post Title</a>'
            '<a href="https://list.example.com/unsubscribe?x=1">Unsubscribe</a>'
            '<a href="https://facebook.com/sharer?u=1">Share</a>')
    links = classify.extract_links(html, "")
    check("extracts the real article link with anchor text",
          links == [{"url": "https://a.com/2026-08-05-real-post", "anchor_text": "Real Post Title"}],
          links)
    check("filters out unsubscribe and social-share links", len(links) == 1, links)
    text_links = classify.extract_links("", "check this out https://a.com/x and https://a.com/x again")
    check("text-fallback dedupes bare urls", len(text_links) == 1, text_links)

    section("newsletters / unsubscribe")
    url = unsub.find_unsubscribe_url("", '<a href="https://list.x.com/unsubscribe?id=9">Unsubscribe</a>')
    check("finds unsubscribe link in html", url == "https://list.x.com/unsubscribe?id=9", url)
    check("no link found returns None", unsub.find_unsubscribe_url("", "<p>no links here</p>") is None)
    check("plain endpoint treated as one-click",
          unsub.is_one_click("https://list.x.com/unsubscribe?id=9"))
    check("click-tracker domain not treated as one-click",
          not unsub.is_one_click("https://click.email.bbc.com/?qs=abc"))

    section("newsletters / unsubscribe - SSRF hardening")
    check("http (non-https) rejected", not unsub._is_safe_target("http://example.com/unsub"))
    check("loopback IP literal rejected", not unsub._is_safe_target("https://127.0.0.1/x"))
    check("cloud metadata IP literal rejected",
          not unsub._is_safe_target("https://169.254.169.254/latest/meta-data"))
    check("private IP literal rejected", not unsub._is_safe_target("https://10.0.0.5/x"))
    check("localhost hostname rejected", not unsub._is_safe_target("https://localhost/x"))

    real_getaddrinfo = socket.getaddrinfo
    try:
        socket.getaddrinfo = lambda host, port: [(None, None, None, None, ("93.184.216.34", 0))]
        check("a host that resolves publicly is a safe target",
              unsub._is_safe_target("https://example.com/unsub"))
        socket.getaddrinfo = lambda host, port: (_ for _ in ()).throw(OSError("no such host"))
        check("a DNS resolution failure is rejected",
              not unsub._is_safe_target("https://does-not-resolve.example/x"))
    finally:
        socket.getaddrinfo = real_getaddrinfo

    section("newsletters / unsubscribe - redirect re-validation")
    handler = unsub._SafeRedirectHandler()
    fake_req = _urllib_request.Request("https://example.org/unsub")
    try:
        handler.redirect_request(fake_req, None, 302, "Found", {},
                                  "https://169.254.169.254/latest/meta-data")
        check("redirect to a metadata IP literal raises", False)
    except _urllib_error.HTTPError:
        check("redirect to a metadata IP literal raises", True)
    new_req = handler.redirect_request(fake_req, None, 302, "Found", {},
                                        "https://93.184.216.34/next")
    check("redirect to a safe IP literal is allowed through",
          new_req is not None and new_req.full_url == "https://93.184.216.34/next")

    section("newsletters / agentmail_client - pagination cap")
    real_urlopen = _urllib_request.urlopen
    calls = {"n": 0}

    def _always_more_pages(req, timeout=None):
        calls["n"] += 1
        body = json.dumps({
            "messages": [{"thread_id": f"t{calls['n']}", "from": "a@x.com"}],
            "next_page_token": "always-more",
        }).encode()
        return _FakeHTTPResponse(body)

    try:
        _urllib_request.urlopen = _always_more_pages
        result = agentmail_client.list_messages("inbox1", "key", max_pages=3)
        check("stops after max_pages even with a non-advancing token",
              calls["n"] == 3, calls["n"])
        check("collects messages from every page fetched", len(result) == 3, len(result))
    finally:
        _urllib_request.urlopen = real_urlopen

    section("newsletters / registry reconciliation")
    registry = [{"sender": "a@x.com", "lastSeen": "2026-01-01"}]
    updated, added = newsletters.reconcile_registry(
        list(registry),
        {"a@x.com": "2026-08-05T07:00:00Z", "b@y.com": "2026-08-05T07:00:00Z"},
        "2026-08-05")
    check("bumps lastSeen for an existing sender",
          next(n for n in updated if n["sender"] == "a@x.com")["lastSeen"] == "2026-08-05")
    check("adds a new sender with defaults",
          "b@y.com" in added and any(n["sender"] == "b@y.com" for n in updated))


def test_newsletter_title_no_url():
    section("newsletters / scan - title never falls back to the raw url (D1)")
    # extract_links' plain-text-only fallback path always returns
    # anchor_text == "" (no html to pull anchor text from) — this is the
    # common case that produced a title-is-a-url item in production.
    real_list_messages = agentmail_client.list_messages
    real_get_thread = agentmail_client.get_thread
    dated_url = f"https://bytes.dev/{NOW.year:04d}/{NOW.month:02d}/{NOW.day:02d}/archives-419"
    try:
        agentmail_client.list_messages = lambda inbox, key, **kw: [
            {"thread_id": "t1", "from": "news@bytes.dev",
             "timestamp": utils.iso(NOW - timedelta(hours=1)), "subject": "Bytes #419"}]
        agentmail_client.get_thread = lambda inbox, tid, key: {
            "subject": "Bytes #419",
            "messages": [{"text": f"Check this out: {dated_url} " + "x" * 200, "html": ""}],
        }
        result = newsletters.scan("inbox1", "key", NOW - timedelta(hours=24), verbose=False)
        check("scan produced exactly one item", len(result["items"]) == 1, result)
        item = result["items"][0]
        check("title is not the raw url", item["title"] != item["url"], item)
        check("title has no http substring", "http" not in item["title"], item)
        check("title falls back to the url's slug words, not 'Untitled link' "
              "(a real path exists to derive words from)",
              item["title"] == utils.slug_words(item["url"]) and item["title"] != "", item)
    finally:
        agentmail_client.list_messages = real_list_messages
        agentmail_client.get_thread = real_get_thread


def test_rank():
    section("rank / merge - assemble")
    fresh_iso = utils.iso(NOW - timedelta(hours=1))
    stale_iso = utils.iso(NOW - timedelta(hours=30))

    feed_items = [
        utils.item(source="dev.to", title="Fresh", url="https://a.com/1",
                   published_at=fresh_iso, paywalled=False),
        utils.item(source="medium", title="Paywalled", url="https://medium.com/2",
                   published_at=fresh_iso),
        utils.item(source="dev.to", title="Stale", url="https://a.com/3",
                   published_at=stale_iso, paywalled=False),
    ]
    news_items = [
        utils.item(source="newsletter:x", title="Dup", url="https://a.com/1", published_at=fresh_iso),
        utils.item(source="newsletter:x", title="New", url="https://a.com/4", published_at=fresh_iso),
    ]
    merged = rank_merge.assemble(feed_items, news_items, CUTOFF)
    check("drops paywalled items", all("medium.com" not in i["url"] for i in merged), merged)
    check("drops stale items", all("/3" not in i["url"] for i in merged))
    check("dedupes by url, feed item wins",
          len(merged) == 2 and merged[0]["source"] == "dev.to", merged)
    check("keeps unique newsletter item", any(i["url"] == "https://a.com/4" for i in merged))

    unsafe_scheme_items = [utils.item(source="x", title="T", url="javascript:alert(1)",
                                       published_at=fresh_iso)]
    merged_unsafe = rank_merge.assemble(unsafe_scheme_items, [], CUTOFF)
    check("drops non-http(s) scheme urls (never becomes a live <a href>)",
          merged_unsafe == [], merged_unsafe)

    merged_allowed = rank_merge.assemble(feed_items, news_items, CUTOFF, allow_paywalled=True)
    check("allow_paywalled=True keeps paywalled items",
          any("medium.com" in i["url"] for i in merged_allowed), merged_allowed)


def test_pools():
    section("rank / pools - build_pool2")
    fresh_iso = utils.iso(NOW - timedelta(hours=1))
    stale_iso = utils.iso(NOW - timedelta(hours=30))

    def devto(n, reactions):
        return utils.item(source="dev.to", title=f"dev {n}", url=f"https://dev.to/{n}",
                          published_at=fresh_iso, paywalled=False, reactions=reactions)

    def hn(n, score):
        return utils.item(source="hacker_news", title=f"hn {n}", url=f"https://ex.com/{n}",
                          published_at=fresh_iso, paywalled=False, score=score)

    feed_items = (
        [devto(i, 100 - i) for i in range(30)]     # 30 dev.to items, all above the floor
        + [devto("low", 1)]                          # below min_reactions=2
        + [hn(i, 100 - i) for i in range(30)]         # 30 hn items, all above the floor
        + [hn("low", 5)]                             # below min_score=40
        + [utils.item(source="medium", title="M", url="https://medium.com/x",
                       published_at=fresh_iso, paywalled=True)]
        + [utils.item(source="dev.to", title="Stale", url="https://dev.to/stale",
                       published_at=stale_iso, paywalled=False, reactions=999)]
        + [devto(0, 100)]  # duplicate url of dev.to/0 -> deduped by merge.assemble
    )
    news_items = [
        utils.item(source="newsletter:x", title="N1", url="https://newsletter.example/1",
                    published_at=fresh_iso),
        utils.item(source="newsletter:y", title="N2", url="https://newsletter.example/2",
                    published_at=fresh_iso),
    ]

    cfg = {
        "pools": {
            "allow_paywalled": True,
            "pool2": {
                "dev.to": {"min_reactions": 2, "cap": 25, "sort_key": "reactions"},
                "hacker_news": {"min_score": 40, "cap": 25, "sort_key": "score"},
                "medium": {"cap": None},
                "newsletter": {"cap": 1},
            },
        }
    }
    pool2 = rank_pools.build_pool2(feed_items, news_items, CUTOFF, cfg)
    by_source = {}
    for it in pool2:
        source = it["source"]
        key = "newsletter" if source.startswith("newsletter:") else source
        by_source.setdefault(key, []).append(it)

    check("dev.to capped at 25", len(by_source.get("dev.to", [])) == 25,
          len(by_source.get("dev.to", [])))
    check("dev.to below min_reactions floor dropped",
          all(i["url"] != "https://dev.to/low" for i in by_source.get("dev.to", [])))
    check("dev.to sorted by reactions desc",
          [i["reactions"] for i in by_source["dev.to"]]
          == sorted((i["reactions"] for i in by_source["dev.to"]), reverse=True))
    check("stale dev.to item dropped",
          all(i["url"] != "https://dev.to/stale" for i in pool2))
    check("dedupes dev.to/0 duplicate url", len(by_source["dev.to"]) == 25)

    check("hacker_news capped at 25", len(by_source.get("hacker_news", [])) == 25,
          len(by_source.get("hacker_news", [])))
    check("hacker_news below min_score floor dropped",
          all(i["url"] != "https://ex.com/low" for i in by_source.get("hacker_news", [])))

    check("medium passes through uncapped despite paywalled (allow_paywalled=True)",
          len(by_source.get("medium", [])) == 1, by_source.get("medium"))
    check("newsletter:<sender> items grouped under one 'newsletter' config rule and capped",
          len(by_source.get("newsletter", [])) == 1, by_source.get("newsletter"))

    check("every pool2 item carries a pool2_rank", all("pool2_rank" in i for i in pool2))

    section("rank / pools - source vs module NAME mismatch")
    # feeds/dev_to.py's module NAME is "dev_to" but items carry source="dev.to";
    # feeds/hacker_news.py's NAME and source both happen to be "hacker_news".
    # build_pool2 must key config off item["source"], never a module's NAME.
    check("dev.to config rule (keyed on item source, not module NAME 'dev_to') was applied",
          len(by_source.get("dev.to", [])) == 25 and "dev_to" not in by_source)


def test_pools_floors():
    section("rank / pools - min_<field> floors on <field>, independent of sort_key (D8)")
    fresh_iso = utils.iso(NOW - timedelta(hours=1))

    def custom(n, score):
        # note: no 'points' field at all — the old bug filtered on
        # sort_key's field name ('points') rather than the field named by
        # the min_* key ('score'), so with no 'points' field every item
        # would have been dropped (or the floor silently ignored with no
        # sort_key at all).
        return utils.item(source="custom_src", title=f"c{n}", url=f"https://x.com/c{n}",
                          published_at=fresh_iso, score=score)

    items = [custom(i, score) for i, score in enumerate([10, 20, 30, 40, 50])]

    cfg_wrong_field_bug = {"pools": {"pool2": {
        "custom_src": {"min_score": 40, "sort_key": "points", "cap": None}}}}
    pool2_a = rank_pools.build_pool2(list(items), [], CUTOFF, cfg_wrong_field_bug)
    check("floor filters on 'score' (derived from the min_score key), not on "
          "sort_key='points' — 2 of 5 survive (score 40, 50)",
          len(pool2_a) == 2 and all(i["score"] >= 40 for i in pool2_a), pool2_a)

    cfg_no_sort_key = {"pools": {"pool2": {
        "custom_src": {"min_score": 40, "cap": None}}}}
    pool2_b = rank_pools.build_pool2(list(items), [], CUTOFF, cfg_no_sort_key)
    check("floor still applies with no sort_key present at all",
          len(pool2_b) == 2 and all(i["score"] >= 40 for i in pool2_b), pool2_b)

    for it in items:
        it["reactions"] = 100 if it["score"] >= 30 else 0
    cfg_two_floors = {"pools": {"pool2": {
        "custom_src": {"min_score": 20, "min_reactions": 100, "cap": None}}}}
    pool2_c = rank_pools.build_pool2(list(items), [], CUTOFF, cfg_two_floors)
    check("two independent min_* rules on the same rule both apply "
          "(score>=20 keeps 4, reactions>=100 further narrows to 3)",
          len(pool2_c) == 3
          and all(i["score"] >= 20 and i.get("reactions", 0) >= 100 for i in pool2_c),
          pool2_c)


def test_enrich():
    section("rank / enrich - clean_for_summary")
    md = ("# Heading\n\nSome **bold** and *italic* text with `code` and a "
          "[link](https://example.com/x) and a bare https://example.com/y url.\n\n"
          "```python\nprint('hi')\n```\n\n"
          "![alt text](https://example.com/img.png)\n\n"
          "- bullet one\n- bullet two\n1. numbered\n")
    cleaned = rank_enrich.clean_for_summary(md)
    check("code fence stripped", "print(" not in cleaned, cleaned)
    check("image stripped", "img.png" not in cleaned, cleaned)
    check("markdown link keeps link text, drops url",
          "link" in cleaned and "example.com/x" not in cleaned, cleaned)
    check("heading marker stripped", "#" not in cleaned, cleaned)
    check("list bullets stripped",
          "- bullet" not in cleaned and "1. numbered" not in cleaned, cleaned)
    check("emphasis markers stripped", "*" not in cleaned and "`" not in cleaned, cleaned)
    check("no raw http substring survives anywhere", "http" not in cleaned, cleaned)
    check("empty/None input handled",
          rank_enrich.clean_for_summary("") == "" and rank_enrich.clean_for_summary(None) == "")

    section("rank / enrich - clean_for_summary preserves code identifiers (D4)")
    ident_md = ("Call `ensure_text` on the pool, tune `max_per_source` in "
                "config, and remember `__init__` runs first. In bare prose, "
                "snake_case_name is still readable. "
                "**bold text** and __also bold__ and *italic* should still go.")
    ident_cleaned = rank_enrich.clean_for_summary(ident_md)
    check("backtick-fenced identifier survives whole: `ensure_text`",
          "ensure_text" in ident_cleaned and "ensuretext" not in ident_cleaned, ident_cleaned)
    check("backtick-fenced identifier survives whole: `max_per_source`",
          "max_per_source" in ident_cleaned and "maxpersource" not in ident_cleaned, ident_cleaned)
    check("backtick-fenced dunder survives whole: `__init__`",
          "__init__" in ident_cleaned, ident_cleaned)
    check("bare-prose snake_case identifier (internal underscores, no backticks) survives",
          "snake_case_name" in ident_cleaned, ident_cleaned)
    check("markdown emphasis (** and __ and *) is still stripped",
          "**bold text**" not in ident_cleaned and "__also bold__" not in ident_cleaned
          and "*italic*" not in ident_cleaned, ident_cleaned)
    check("stray backticks don't survive outside identifiers", "`" not in ident_cleaned, ident_cleaned)

    section("rank / enrich - ensure_text resolution order + idempotency")
    real_body_fetcher = feeds.body_fetcher
    real_http_get = utils.http_get
    real_extract = rank_enrich.trafilatura.extract
    hook_calls = {"n": 0}
    http_calls = {"n": 0}

    def fake_body_fetcher(source):
        if source != "hook_source":
            return None

        def hook(item):
            hook_calls["n"] += 1
            return "text from feed hook"
        return hook

    def fake_http_get(url, as_json=False, timeout=None):
        http_calls["n"] += 1
        if "fails" in url:
            raise RuntimeError("network down")
        return "<html><body>raw html</body></html>"

    def fake_extract(html, **kw):
        return "text from generic extraction"

    try:
        feeds.body_fetcher = fake_body_fetcher
        utils.http_get = fake_http_get
        rank_enrich.trafilatura.extract = fake_extract

        item_hook = {"source": "hook_source", "url": "https://a.com/hook"}
        item_excerpt = {"source": "no_hook", "url": "https://a.com/excerpt",
                         "body_excerpt": "text from excerpt"}
        item_generic = {"source": "no_hook", "url": "https://a.com/generic"}
        item_failing = {"source": "no_hook", "url": "https://a.com/fails"}
        items = [item_hook, item_excerpt, item_generic, item_failing]

        errors = rank_enrich.ensure_text(items, workers=4)

        check("feed hook wins over body_excerpt and generic extraction",
              item_hook.get("article_text") == "text from feed hook", item_hook)
        check("body_excerpt wins over generic extraction when no hook",
              item_excerpt.get("article_text") == "text from excerpt", item_excerpt)
        check("generic http+trafilatura extraction used with no hook and no body_excerpt",
              item_generic.get("article_text") == "text from generic extraction", item_generic)
        check("a failing fetch records an error and leaves the item usable (no article_text, no raise)",
              "article_text" not in item_failing and any("fails" in e for e in errors), errors)
        check("first pass: hook called once, generic http_get called for the 2 no-hook/no-excerpt items",
              hook_calls["n"] == 1 and http_calls["n"] == 2, (hook_calls, http_calls))

        errors2 = rank_enrich.ensure_text(items, workers=4)
        check("idempotent: already-resolved items are not refetched on a second call",
              hook_calls["n"] == 1 and http_calls["n"] == 3, (hook_calls, http_calls))
        check("second call retries only the still-unresolved item",
              "article_text" not in item_failing and errors2, errors2)
    finally:
        feeds.body_fetcher = real_body_fetcher
        utils.http_get = real_http_get
        rank_enrich.trafilatura.extract = real_extract


def test_enrich_hook_raises():
    section("rank / enrich - a raising feed hook falls through instead of abandoning the item (D3)")
    real_body_fetcher = feeds.body_fetcher
    try:
        def raising_hook(item):
            raise RuntimeError("429 Too Many Requests")

        feeds.body_fetcher = lambda source: raising_hook if source == "dev.to" else None

        item = {"source": "dev.to", "url": "https://dev.to/x",
                "body_excerpt": "fallback text sitting right here"}
        errors = rank_enrich.ensure_text([item], workers=2)

        check("article_text is set from body_excerpt despite the hook raising",
              item.get("article_text") == "fallback text sitting right here", item)
        check("no error is recorded when the fallback path succeeds", errors == [], errors)
    finally:
        feeds.body_fetcher = real_body_fetcher


def test_enrich_skip_sources():
    section("rank / enrich - skip_sources is never even attempted, no error recorded (D10)")
    real_http_get = utils.http_get
    real_body_fetcher = feeds.body_fetcher
    calls = {"n": 0}
    try:
        feeds.body_fetcher = lambda source: None

        def counting_http_get(url, as_json=False, timeout=None):
            calls["n"] += 1
            return "<html></html>"

        utils.http_get = counting_http_get
        # Medium 403s our fetcher every time in practice — skip_sources
        # stops the pipeline from even trying, rather than paying for a
        # futile fetch and recording a permanent error every run.
        item = {"source": "medium", "url": "https://medium.com/x"}
        errors = rank_enrich.ensure_text([item], skip_sources=["medium"], workers=2)

        check("a skipped source is never fetched", calls["n"] == 0, calls)
        check("no error is recorded for a skipped item", errors == [], errors)
        check("a skipped item is left without article_text "
              "(downstream falls back to description/title)",
              "article_text" not in item, item)
    finally:
        utils.http_get = real_http_get
        feeds.body_fetcher = real_body_fetcher


def test_interests_parse():
    section("rank / relevance - parse_interests (real interests.md)")
    interests_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "reading-hub", "interests.md")
    with open(interests_path, encoding="utf-8") as f:
        real_interests = f.read()
    parsed = rank_relevance.parse_interests(real_interests)

    check("extracts all 6 topic bullets", len(parsed["topics"]) == 6, parsed["topics"])
    weights = [w for _, w in parsed["topics"]]
    check("High/Medium/Low priority markers map to 1.0/0.7/0.4",
          weights == [1.0, 1.0, 1.0, 0.7, 0.7, 0.4], weights)
    check("emphasis markers stripped from topic query text",
          all("*" not in t for t, _ in parsed["topics"]), parsed["topics"])
    check("no topic query text contains the priority word itself (D5 — 'High'/'Medium'/"
          "'Low' is metadata, not query text, and 'Medium' collides with a source name)",
          all(w not in t for t, _ in parsed["topics"] for w in ("High", "Medium", "Low")),
          parsed["topics"])
    check("'## My stack' (lowercase heading) bullets extracted",
          len(parsed["stack"]) == 4, parsed["stack"])
    check("dial up bullets extracted", len(parsed["dial_up"]) == 5, parsed["dial_up"])
    check("dial down bullets extracted", len(parsed["dial_down"]) == 2, parsed["dial_down"])

    section("rank / relevance - parse_interests (missing sections)")
    empty = rank_relevance.parse_interests("# Just a title\n\nNo sections here.\n")
    check("missing sections default to empty lists, never a crash",
          empty == {"topics": [], "stack": [], "dial_up": [], "dial_down": []}, empty)
    check("empty string input doesn't crash", rank_relevance.parse_interests("") == empty)


_INTERESTS_STUB = """\
## Topics

- Topic A — *High*
- Topic B — *Low*

## My stack

- Stack skill

## Dial up

- Dial up thing

## Dial down

- Dial down thing
"""


def test_doc_text_budget():
    section("rank / relevance - _doc_text prefers whichever of description/"
            "article_text carries more signal (D9)")
    long_body = "x" * 1000
    short_desc = "y" * 50
    item_body_wins = utils.item(source="s", title="T", url="https://a.com/1",
                                 published_at="", description=short_desc)
    item_body_wins["article_text"] = long_body
    text = rank_relevance._doc_text(item_body_wins, body_chars=600)
    check("a long article_text wins over a short description",
          "x" * 600 in text and short_desc not in text, text[:80])
    check("the chosen text is truncated to body_chars",
          len(text.split(" — ")[-1]) == 600, len(text.split(" — ")[-1]))

    item_desc_wins = utils.item(source="s", title="T", url="https://a.com/2",
                                 published_at="", description="d" * 500)
    item_desc_wins["article_text"] = "a" * 10
    text2 = rank_relevance._doc_text(item_desc_wins, body_chars=600)
    check("a longer description wins over a short article_text (v1's old "
          "unconditional description-first behaviour is now length-based)",
          "d" * 500 in text2 and "a" * 10 not in text2, text2[:80])

    empty_item = {"title": "", "tags": [], "url": "https://a.com/some/real/path"}
    text3 = rank_relevance._doc_text(empty_item)
    check("an item with no title/tags/text falls back to url slug words",
          text3 == utils.slug_words("https://a.com/some/real/path") and text3 != "", text3)


def test_relevance_math():
    section("rank / relevance - weighting arithmetic + non-tech drop rule")
    # A 5-axis basis: [topic, stack, dial_up, dial_down, non_tech]. Anchors
    # are pure basis vectors so a doc's cosine similarity to an anchor, once
    # normalised, is exactly that doc's component along the axis divided by
    # its norm — letting the expected score be computed by hand.
    e_topic = [1, 0, 0, 0, 0]
    e_stack = [0, 1, 0, 0, 0]
    e_dial_up = [0, 0, 1, 0, 0]
    e_dial_down = [0, 0, 0, 1, 0]
    e_non_tech = [0, 0, 0, 0, 1]

    v_survivor = [0.6, 0.3, 0.2, 0.0, 0.1]     # topic-heavy -> should survive, real score
    v_nontech = [0.1, 0, 0, 0, 0.5]            # non_tech > topic -> should be dropped

    items_math = [
        utils.item(source="keep_src", title="x", url="https://x.com/1", published_at=""),
        utils.item(source="drop_src", title="x", url="https://x.com/2", published_at=""),
        utils.item(source="exempt_src", title="x", url="https://x.com/3", published_at=""),
    ]
    vectors = [
        v_survivor, v_nontech, v_nontech,          # 3 docs, in items_math order
        e_topic, e_topic,                            # 2 topic anchors (Topic A, Topic B)
        e_stack,                                      # 1 stack anchor
        e_dial_up,                                     # 1 dial_up anchor
        e_dial_down,                                    # 1 dial_down anchor
        e_non_tech, e_non_tech, e_non_tech, e_non_tech, e_non_tech,  # 5 NON_TECH_ANCHORS
    ]

    def stub_encode(texts):
        check("stub encoder receives the expected text count", len(texts) == len(vectors),
              (len(texts), len(vectors)))
        return np.asarray(vectors, dtype=float)

    cfg = {
        "relevance": {
            "weights": {"stack": 0.30, "dial_up": 0.35, "dial_down": 0.60},
            "drop_non_tech": True,
            "never_drop_sources": ["exempt_src"],
        },
        "pools": {"pool3": {"size": 10, "max_per_source": {}}},
    }
    pool3, dropped = rank_relevance.rank(items_math, _INTERESTS_STUB, cfg, encode=stub_encode)

    check("exactly one item dropped (non-tech, not exempt)",
          len(dropped) == 1 and dropped[0]["source"] == "drop_src", dropped)
    check("never_drop_sources exempts a source from the non-tech drop",
          any(it["source"] == "exempt_src" for it in pool3), pool3)
    check("pool3 keeps the non-dropped items", len(pool3) == 2, pool3)

    survivor = next(it for it in pool3 if it["source"] == "keep_src")
    norm = math.sqrt(sum(x * x for x in v_survivor))
    topic_sim = v_survivor[0] / norm
    stack_sim = v_survivor[1] / norm
    dial_up_sim = v_survivor[2] / norm
    dial_down_sim = v_survivor[3] / norm
    expected_score = topic_sim + 0.30 * stack_sim + 0.35 * dial_up_sim - 0.60 * dial_down_sim
    check("score matches topic + w_stack*stack + w_dial_up*dial_up - w_dial_down*dial_down",
          math.isclose(survivor["relevance"]["score"], expected_score, rel_tol=1e-6),
          (survivor["relevance"], expected_score))

    section("rank / relevance - per-source cap is a backstop, not a hard filter")
    big = [utils.item(source="big_src", title="b", url=f"https://x.com/big{i}", published_at="")
           for i in range(4)]
    small = [utils.item(source="small_src", title="s", url=f"https://x.com/small{i}", published_at="")
             for i in range(2)]
    items_cap = big + small
    vectors_cap = (
        [e_topic] * 4 + [e_stack] * 2       # 6 docs: 4 big (topic axis), 2 small (stack axis)
        + [e_topic, e_topic] + [e_stack] + [e_dial_up] + [e_dial_down]  # anchors
        + [e_non_tech] * 5
    )

    def stub_encode_cap(texts):
        return np.asarray(vectors_cap, dtype=float)

    cfg_cap = {
        "relevance": {"weights": {"stack": 0.30, "dial_up": 0.35, "dial_down": 0.60},
                       "drop_non_tech": True, "never_drop_sources": []},
        "pools": {"pool3": {"size": 5, "max_per_source": {"big_src": 2}}},
    }
    pool3_cap, dropped_cap = rank_relevance.rank(items_cap, _INTERESTS_STUB, cfg_cap,
                                                   encode=stub_encode_cap)
    check("nothing dropped as non-tech in the cap scenario", dropped_cap == [], dropped_cap)
    check("pool still fills to size even though a cap would otherwise starve it",
          len(pool3_cap) == 5, len(pool3_cap))
    big_in_pool = sum(1 for it in pool3_cap if it["source"] == "big_src")
    check("cap is a backstop, not a hard shrink: 3 big_src items survive though cap is 2 "
          "(2 respecting the cap + 1 filled back in to reach pool size)",
          big_in_pool == 3, big_in_pool)
    check("both small_src items included (uncapped)",
          sum(1 for it in pool3_cap if it["source"] == "small_src") == 2)


def test_relevance_fallback():
    section("rank / relevance - fallback on encoder failure")

    def raising_encode(texts):
        raise RuntimeError("simulated: HF hub unreachable")

    items = []
    for src, n in (("dev.to", 30), ("hacker_news", 5)):
        for i in range(n):
            it = utils.item(source=src, title=f"{src} {i}", url=f"https://x.com/{src}/{i}",
                            published_at="")
            it["pool2_rank"] = i
            items.append(it)

    cfg = {
        "relevance": {"weights": {"stack": 0.3, "dial_up": 0.35, "dial_down": 0.6},
                       "drop_non_tech": True, "never_drop_sources": []},
        "pools": {"pool3": {"size": 25, "max_per_source": {"dev.to": 10}}},
    }

    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        pool3, dropped = rank_relevance.rank(items, _INTERESTS_STUB, cfg, encode=raising_encode)

    check("no exception escapes an encoder failure", True)
    check("dropped is empty on fallback (no scoring happened)", dropped == [])
    check("fallback still fills to pool3.size", len(pool3) == 25, len(pool3))
    check("the encoder failure is logged", "falling back" in buf.getvalue(), buf.getvalue())
    check("every fallback item is marked relevance.fallback=True (D7 — distinguishes "
          "'no scoring happened' from a real score of 0.0)",
          all(it.get("relevance", {}).get("fallback") is True for it in pool3),
          [it.get("relevance") for it in pool3])

    by_source: dict[str, list[int]] = {}
    for it in pool3:
        by_source.setdefault(it["source"], []).append(it["pool2_rank"])
    check("fallback ordering respects each source's existing engagement order (pool2_rank)",
          all(ranks == sorted(ranks) for ranks in by_source.values()), by_source)
    check("max_per_source is honoured in the fallback path too (D7): dev.to capped at 10 "
          "respecting the cap, backstop-filled to still reach pool3.size, hacker_news "
          "(uncapped, only 5 available) takes all of them",
          len(by_source.get("dev.to", [])) == 20 and len(by_source.get("hacker_news", [])) == 5,
          by_source)


def test_relevance_scoring_errors_propagate():
    section("rank / relevance - a bug after encode() raises, not silently swallowed (D6)")
    items = [utils.item(source="s1", title="x", url="https://x.com/1", published_at="")]

    def ragged_encode(texts):
        # A malformed encoder output is a real bug — wrong per-text vector
        # lengths — not an "embedding model unavailable" situation, and
        # must not be caught by the same broad except that guards the
        # actual model call.
        return [[1.0, 2.0], [1.0, 2.0, 3.0]]

    cfg = {
        "relevance": {"weights": {"stack": 0.3, "dial_up": 0.35, "dial_down": 0.6},
                       "drop_non_tech": True, "never_drop_sources": []},
        "pools": {"pool3": {"size": 10, "max_per_source": {}}},
    }
    try:
        rank_relevance.rank(items, _INTERESTS_STUB, cfg, encode=ragged_encode)
        check("a malformed encoder output raises instead of silently falling back", False)
    except (ValueError, TypeError):
        check("a malformed encoder output raises instead of silently falling back", True)


def test_relevance_no_topics():
    section("rank / relevance - unparseable '## Topics' disables the non-tech drop (D2)")
    # A renamed heading ("## Topics & priorities", a stray "###", etc.)
    # parses to zero topic bullets, which used to make raw_topic_sim == 0.0
    # for every item -> the non-tech drop rule (non_tech_sim > raw_topic_sim)
    # fired on the entire pool, since real non-tech similarities are never
    # exactly zero. The fix disables the rule instead of letting it eat
    # everything.
    items = [
        utils.item(source="s1", title="a tech article", url="https://x.com/1", published_at=""),
        utils.item(source="s2", title="a sports article", url="https://x.com/2", published_at=""),
    ]
    interests_no_topics = "## My stack\n\n- Node\n\n## Dial up\n\n- AI\n\n## Dial down\n\n- politics\n"
    cfg = {
        "relevance": {"weights": {"stack": 0.3, "dial_up": 0.35, "dial_down": 0.6},
                       "drop_non_tech": True, "never_drop_sources": []},
        "pools": {"pool3": {"size": 10, "max_per_source": {}}},
    }

    def stub_encode(texts):
        return np.ones((len(texts), 3))

    parsed = rank_relevance.parse_interests(interests_no_topics)
    check("setup: this interests text really does parse to zero topics",
          parsed["topics"] == [], parsed)

    pool3, dropped = rank_relevance.rank(items, interests_no_topics, cfg, encode=stub_encode)
    check("nothing dropped as non-tech when Topics can't be parsed", dropped == [], dropped)
    check("pool3 still fills with min(size, len(items)) instead of emptying",
          len(pool3) == len(items), pool3)


def test_prompt():
    section("rank / prompt - build_digest_prompt")
    items = [
        utils.item(source="dev.to", title="Item A", url="https://a.com/1",
                    published_at="2026-08-05T00:00:00Z", tags=["react"]),
        utils.item(source="hacker_news", title="Item B", url="https://b.com/2",
                    published_at="2026-08-05T00:00:00Z", tags=[]),
    ]
    items[0]["summary"] = "A summary with no link syntax."
    items[1]["summary"] = "Another clean summary."

    prompt = rank_prompt.build_digest_prompt("# Interests\nHigh: craft", items, 9)

    check("prompt embeds target count", "9" in prompt, prompt[:200])
    check("prompt embeds interests text", "Interests" in prompt)
    check("prompt embeds every item's summary",
          all(it["summary"] in prompt for it in items))
    check("no url appears anywhere in the prompt",
          not any(it["url"] in prompt for it in items), prompt)
    check("no published_at appears anywhere in the prompt",
          "2026-08-05T00:00:00Z" not in prompt, prompt)
    check("no raw http substring anywhere in the prompt payload section",
          "http" not in prompt.split("=== CANDIDATES")[1].split("=== END CANDIDATES")[0])
    check("indices are contiguous from 0",
          '"i": 0' in prompt and '"i": 1' in prompt)


def test_prompt_url_scrub():
    section("rank / prompt - _scrub boundary guard (D1)")
    # Reproduces the real production scenario: a newsletter item whose title
    # IS a url (enrichment failed on it too), a tag containing a url, and a
    # summary that is nothing but a url. None of clean_for_summary,
    # pick_text, or upstream fixes are relied on here — build_digest_prompt
    # must be safe even if every upstream layer already failed.
    items = [{
        "source": "newsletter:news@bytes.dev",
        "title": "https://bytes.dev/archives/419",
        "url": "https://bytes.dev/archives/419",
        "tags": ["see https://tracker.example/x"],
        "summary": "https://bytes.dev/archives/419",
    }]
    prompt = rank_prompt.build_digest_prompt("# Interests", items, 9)
    check("no http substring anywhere in the prompt", "http" not in prompt, prompt)
    check("no www. substring anywhere in the prompt", "www." not in prompt, prompt)

    section("rank / prompt - _scrub falls back to slug words, never an empty title")
    check("_scrub of a pure-url string is empty", rank_prompt._scrub("https://a.com/x") == "")
    only_url_title = [{"source": "s", "title": "https://a.com/some/real/path",
                        "url": "https://a.com/some/real/path", "tags": [], "summary": "ok"}]
    prompt2 = rank_prompt.build_digest_prompt("# Interests", only_url_title, 9)
    check("a title that scrubs to empty falls back to url slug words, not blank",
          '"title": ""' not in prompt2 and "real" in prompt2 and "path" in prompt2, prompt2)


class _FakeHTTPResponse:
    def __init__(self, body):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _EnvKeys:
    """Set only the given env vars for the block, restoring prior state after."""

    def __init__(self, **keys):
        self.keys = keys
        self.saved = {}

    def __enter__(self):
        for k in llm_client.PROVIDER_ENV_VARS:
            self.saved[k] = os.environ.pop(k, None)
        for k, v in self.keys.items():
            if v is not None:
                os.environ[k] = v
        return self

    def __exit__(self, *a):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return False


def test_llm_client():
    section("rank / llm_client")
    real_urlopen = _urllib_request.urlopen
    good_gemini_body = json.dumps({
        "candidates": [{"content": {"parts": [{"text": json.dumps({"ok": True})}]}}]
    }).encode()
    good_openai_body = json.dumps({
        "choices": [{"message": {"content": json.dumps({"ok": True})}}]
    }).encode()
    try:
        with _EnvKeys(GEMINI_API_KEY="k"):
            _urllib_request.urlopen = lambda req, timeout=None: _FakeHTTPResponse(good_gemini_body)
            result = llm_client.generate_json("prompt", config={})
            check("parses nested candidate JSON text", result == {"ok": True}, result)

            bad_body = json.dumps({
                "candidates": [{"content": {"parts": [{"text": "not json"}]}}]
            }).encode()
            _urllib_request.urlopen = lambda req, timeout=None: _FakeHTTPResponse(bad_body)
            try:
                llm_client.generate_json("prompt", config={})
                check("raises on invalid JSON text", False)
            except RuntimeError:
                check("raises on invalid JSON text", True)

            _urllib_request.urlopen = lambda req, timeout=None: _FakeHTTPResponse(b"{}")
            try:
                llm_client.generate_json("prompt", config={})
                check("raises when candidates missing", False)
            except RuntimeError:
                check("raises when candidates missing", True)

        real_sleep = time.sleep
        time.sleep = lambda s: None
        try:
            with _EnvKeys(GEMINI_API_KEY="k"):
                calls = {"n": 0}

                def flaky_then_ok(req, timeout=None):
                    calls["n"] += 1
                    if calls["n"] < 2:
                        raise TimeoutError("read timed out")
                    return _FakeHTTPResponse(good_gemini_body)

                _urllib_request.urlopen = flaky_then_ok
                result = llm_client.generate_json("prompt", config={})
                check("retries a read-timeout and succeeds on the next attempt",
                      result == {"ok": True} and calls["n"] == 2, calls)

                calls2 = {"n": 0}

                def always_times_out(req, timeout=None):
                    calls2["n"] += 1
                    raise TimeoutError("read timed out")

                _urllib_request.urlopen = always_times_out
                try:
                    llm_client.generate_json("prompt", config={})
                    check("raises RuntimeError (not bare TimeoutError) after exhausting retries", False)
                except RuntimeError:
                    check("raises RuntimeError (not bare TimeoutError) after exhausting retries",
                          calls2["n"] == llm_client.SERVER_ERROR_ATTEMPTS, calls2)

                calls3 = {"n": 0}

                def bad_request(req, timeout=None):
                    calls3["n"] += 1
                    raise _urllib_error.HTTPError(
                        "https://x", 400, "Bad Request", {}, io.BytesIO(b"bad key"))

                _urllib_request.urlopen = bad_request
                try:
                    llm_client.generate_json("prompt", config={})
                    check("does not retry a non-retryable HTTP 400", False)
                except RuntimeError:
                    check("does not retry a non-retryable HTTP 400", calls3["n"] == 1, calls3)

                calls4 = {"n": 0}

                def always_rate_limited(req, timeout=None):
                    calls4["n"] += 1
                    raise _urllib_error.HTTPError(
                        "https://x", 429, "Too Many Requests", {}, io.BytesIO(b"rate limited"))

                _urllib_request.urlopen = always_rate_limited
                try:
                    llm_client.generate_json("prompt", config={})
                    check("caps 429 retries below the server-error budget (fails over fast)", False)
                except RuntimeError:
                    check("caps 429 retries below the server-error budget (fails over fast)",
                          calls4["n"] == llm_client.RATE_LIMIT_ATTEMPTS, calls4)

            with _EnvKeys(GEMINI_API_KEY="k", GROQ_API_KEY="g"):
                def gemini_fails_groq_succeeds(req, timeout=None):
                    if "generativelanguage.googleapis.com" in req.full_url:
                        raise _urllib_error.HTTPError(
                            "https://x", 503, "Unavailable", {}, io.BytesIO(b"overloaded"))
                    return _FakeHTTPResponse(good_openai_body)

                _urllib_request.urlopen = gemini_fails_groq_succeeds
                result = llm_client.generate_json("prompt", config={})
                check("falls over to the next configured provider when the first is down",
                      result == {"ok": True}, result)

            with _EnvKeys():
                try:
                    llm_client.generate_json("prompt", config={})
                    check("raises with no provider configured", False)
                except RuntimeError as e:
                    check("raises with no provider configured", "no LLM provider configured" in str(e), e)
        finally:
            time.sleep = real_sleep
    finally:
        _urllib_request.urlopen = real_urlopen


def test_pools_json_output():
    section("main / cmd_pools --json output shape, including the fallback path (v1 gap)")
    real_http_get = utils.http_get
    real_rank = rank_relevance.rank
    real_ensure_text = rank_enrich.ensure_text
    real_agentmail_key = os.environ.pop("AGENTMAIL_API_KEY", None)
    real_agentmail_inbox = os.environ.pop("AGENTMAIL_INBOX", None)
    buf = io.StringIO()
    try:
        utils.http_get = fake_get  # feeds/*.py fetch via the shared fixture stub
        rank_enrich.ensure_text = lambda items, **kw: []

        def stub_rank(items, interests_text, cfg, **kw):
            # Simulate the fallback path: pool3 items carry only a partial
            # relevance dict (score + fallback flag, no topic/stack/etc
            # breakdown) — cmd_pools's --json assembly must not choke on
            # that shape, and it should be visibly distinguishable from a
            # real score of 0.0.
            dropped = [dict(items[0], relevance={"score": 0.0, "topic_raw": 0.05,
                                                   "non_tech": 0.2})] if items else []
            pool3 = [dict(it, relevance={"score": 0.0, "fallback": True}) for it in items[:3]]
            return pool3, dropped

        rank_relevance.rank = stub_rank

        args = types.SimpleNamespace(hours=None, json=True, verbose=False)
        with contextlib.redirect_stdout(buf):
            rc = M.cmd_pools(args)

        check("cmd_pools --json returns 0", rc == 0, rc)
        payload = json.loads(buf.getvalue())
        check("payload has exactly the expected top-level keys",
              set(payload) == {"pool2_total", "pool2_by_source", "enrich_errors",
                                "dropped", "pool3"},
              set(payload))
        check("pool3 entries always carry title + source",
              all("title" in e and "source" in e for e in payload["pool3"]), payload["pool3"])
        check("fallback pool3 entries surface fallback=True with no topic/stack breakdown "
              "(the partial-relevance shape doesn't crash the JSON assembly)",
              bool(payload["pool3"])
              and all(e.get("fallback") is True and "topic" not in e for e in payload["pool3"]),
              payload["pool3"])
        check("dropped entries carry whatever relevance fields were set, no crash on a "
              "partial shape (non_tech present, no 'stack'/'dial_up' key)",
              all("non_tech" in e and "stack" not in e for e in payload["dropped"]),
              payload["dropped"])
    finally:
        utils.http_get = real_http_get
        rank_relevance.rank = real_rank
        rank_enrich.ensure_text = real_ensure_text
        if real_agentmail_key is not None:
            os.environ["AGENTMAIL_API_KEY"] = real_agentmail_key
        if real_agentmail_inbox is not None:
            os.environ["AGENTMAIL_INBOX"] = real_agentmail_inbox


def test_digest_helpers():
    section("main / target item count calibration")
    cfg = {"digest": {"target_read_minutes": 30, "fallback_min_per_item": 3.33}}
    log_with_actual = [
        {"date": "baseline", "actualReadMin": None, "items": None},
        {"date": "2026-07-21", "actualReadMin": 30, "items": 9, "minPerItem": 3.33},
        {"date": "2026-07-25", "actualReadMin": 30, "items": 9, "minPerItem": 3.33},
    ]
    target, mpi = M._compute_target_item_count(log_with_actual, cfg)
    check("picks the most recent row with an actual read time", mpi == 3.33, mpi)
    check("computes target from target_read_minutes / min_per_item", target == 9, target)

    empty_log = [{"date": "baseline", "actualReadMin": None, "items": None}]
    target2, mpi2 = M._compute_target_item_count(empty_log, cfg)
    check("falls back to config's fallback_min_per_item with no actuals",
          mpi2 == 3.33 and target2 == 9, (mpi2, target2))

    section("main / reconcile digest against known pool3 items (anti-hallucination, index-keyed)")
    pool3 = [
        {"url": "https://a.com/1", "title": "Real Title", "source": "dev.to",
         "published_at": "2026-08-05T00:00:00Z", "tags": ["ai"], "paywalled": False},
        {"url": "https://b.com/2", "title": "Second Title", "source": "medium",
         "published_at": "2026-08-05T01:00:00Z", "tags": [], "paywalled": True},
    ]
    model_digest = {
        "intro": "today's hook",
        "sections": [{
            "heading": "🤖 Craft",
            "items": [
                # valid index 0 -> factual fields come from pool3[0]; model's
                # url/title are ignored (the model isn't even given a url).
                {"i": 0, "title": "MODEL-CHANGED TITLE", "url": "https://evil.example/fake",
                 "summary": "a real summary"},
                {"i": 5, "summary": "dropped: index out of range"},
                {"i": "0", "summary": "dropped: string index, not int"},
                {"i": 0, "summary": "dropped: duplicate of an already-used index"},
                {"i": True, "summary": "dropped: bool is not a real index"},
            ],
        }],
    }
    reconciled = M._reconcile_digest(model_digest, pool3)
    check("keeps the intro prose", reconciled["intro"] == "today's hook")
    check("keeps exactly one item (out-of-range/non-int/duplicate/bool indices all dropped)",
          len(reconciled["sections"][0]["items"]) == 1, reconciled)
    kept = reconciled["sections"][0]["items"][0]
    check("factual fields come from our own pool3 data by index, not the model",
          kept["title"] == "Real Title" and kept["source"] == "dev.to"
          and kept["url"] == "https://a.com/1"
          and kept["publishedAt"] == "2026-08-05T00:00:00Z" and kept["tags"] == ["ai"], kept)
    check("only the summary is trusted from the model", kept["summary"] == "a real summary")
    check("paywalled is carried through from our data (for the member-only badge)",
          kept["paywalled"] is False)

    all_invalid = M._reconcile_digest(
        {"intro": "x", "sections": [{"heading": "h", "items": [
            {"i": 99, "summary": "s"}]}]}, pool3)
    check("a section with only invalid indices is dropped entirely",
          all_invalid["sections"] == [], all_invalid)


def main():
    test_utils()
    test_discovery()
    test_feeds()
    test_newsletters()
    test_newsletter_title_no_url()
    test_rank()
    test_pools()
    test_pools_floors()
    test_enrich()
    test_enrich_hook_raises()
    test_enrich_skip_sources()
    test_interests_parse()
    test_doc_text_budget()
    test_relevance_math()
    test_relevance_fallback()
    test_relevance_scoring_errors_propagate()
    test_relevance_no_topics()
    test_prompt()
    test_prompt_url_scrub()
    test_llm_client()
    test_pools_json_output()
    test_digest_helpers()
    print(f"\n{COUNT - len(FAILURES)}/{COUNT} passed")
    if FAILURES:
        print("FAILED: " + ", ".join(FAILURES))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
