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

import io
import json
import os
import socket
import sys
import time
import urllib.error as _urllib_error
import urllib.request as _urllib_request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import feeds  # noqa: E402
import main as M  # noqa: E402
import newsletters  # noqa: E402
import utils  # noqa: E402
from feeds import dev_to, hacker_news, medium, pragmatic_engineer  # noqa: E402
from newsletters import agentmail_client  # noqa: E402
from newsletters import classify  # noqa: E402
from newsletters import unsubscribe as unsub  # noqa: E402
from rank import llm_client  # noqa: E402
from rank import merge as rank_merge  # noqa: E402
from rank import prompt as rank_prompt  # noqa: E402

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

    section("rank / merge - trim_for_selection")
    trimmed = rank_merge.trim_for_selection([utils.item(
        source="dev.to", title="T", url="https://a.com/1", published_at=fresh_iso,
        description="x" * 500)], excerpt_chars=50)
    check("trims description to excerpt_chars", len(trimmed[0]["description"]) == 50)
    check("trim keeps only the needed fields",
          set(trimmed[0]) == {"url", "source", "title", "published_at", "tags", "description"})

    section("rank / prompt - templates")
    p1 = rank_prompt.build_selection_prompt("# Interests\nHigh: craft", trimmed, 9)
    check("selection prompt embeds target count", "9" in p1, p1[:200])
    check("selection prompt embeds interests text", "Interests" in p1)
    p2 = rank_prompt.build_summary_prompt([{"section": "Craft", "items": trimmed}])
    check("summary prompt embeds grouped items json",
          "Craft" in p2 and trimmed[0]["url"] in p2)


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

    section("main / reconcile digest against known candidates (anti-hallucination)")
    by_url = {
        "https://a.com/1": {"url": "https://a.com/1", "title": "Real Title",
                             "source": "dev.to", "published_at": "2026-08-05T00:00:00Z",
                             "tags": ["ai"]},
    }
    model_digest = {
        "intro": "today's hook",
        "sections": [{
            "heading": "🤖 Craft",
            "items": [
                # model echoes a known url -> factual fields still come from by_url,
                # only `summary` is trusted from the model.
                {"url": "https://a.com/1", "title": "MODEL-CHANGED TITLE",
                 "source": "MODEL-CHANGED SOURCE", "publishedAt": "2099-01-01T00:00:00Z",
                 "tags": ["hallucinated"], "summary": "a real summary"},
                # model hallucinates a url never in the candidate set -> dropped entirely.
                {"url": "https://evil.example/fake", "title": "Fake", "source": "?",
                 "publishedAt": "2026-08-05T00:00:00Z", "tags": [], "summary": "should be dropped"},
            ],
        }],
    }
    reconciled = M._reconcile_digest(model_digest, by_url)
    check("keeps the intro prose", reconciled["intro"] == "today's hook")
    check("keeps exactly one item (hallucinated url dropped)",
          len(reconciled["sections"][0]["items"]) == 1, reconciled)
    kept = reconciled["sections"][0]["items"][0]
    check("factual fields come from our own candidate data, not the model",
          kept["title"] == "Real Title" and kept["source"] == "dev.to"
          and kept["publishedAt"] == "2026-08-05T00:00:00Z" and kept["tags"] == ["ai"], kept)
    check("only the summary is trusted from the model", kept["summary"] == "a real summary")

    all_hallucinated = M._reconcile_digest(
        {"intro": "x", "sections": [{"heading": "h", "items": [
            {"url": "https://nope.example/x", "summary": "s"}]}]}, by_url)
    check("a section with only unmatched urls is dropped entirely",
          all_hallucinated["sections"] == [], all_hallucinated)


def main():
    test_utils()
    test_discovery()
    test_feeds()
    test_newsletters()
    test_rank()
    test_llm_client()
    test_digest_helpers()
    print(f"\n{COUNT - len(FAILURES)}/{COUNT} passed")
    if FAILURES:
        print("FAILED: " + ", ".join(FAILURES))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
