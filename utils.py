"""Shared helpers for feed modules. No feed-specific logic lives here."""

from __future__ import annotations

import html
import json
import re
import ssl
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) digest-fetcher/2.0"
TIMEOUT = 20

_ctx = ssl.create_default_context()


def log(msg: str, *, verbose: bool = True) -> None:
    if verbose:
        print(f"[digest] {msg}", file=sys.stderr)


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

def http_get(url: str, *, as_json: bool = False, timeout: int = TIMEOUT):
    """One GET. Raises on failure so callers can record the reason."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout, context=_ctx) as r:
        raw = r.read()
    text = raw.decode("utf-8", "replace")
    return json.loads(text) if as_json else text


def parallel(fn, jobs, *, workers: int = 8):
    """Map fn over jobs concurrently. Yields (job, result, error) per job.

    Never raises — a failing job comes back with error set, so one bad source
    can't take down a run.
    """
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fn, j): j for j in jobs}
        for fut in as_completed(futures):
            job = futures[fut]
            try:
                yield job, fut.result(), None
            except Exception as e:  # noqa: BLE001 - deliberately broad
                yield job, None, e


# --------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------

def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat((s or "").replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def parse_rfc822(s: str) -> datetime | None:
    """RSS pubDate. Always returns tz-aware, assuming UTC when unspecified."""
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        return None
    if d is None:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# RSS / HTML
# --------------------------------------------------------------------------

def rss_items(xml: str) -> list[str]:
    return [m.group(1) for m in re.finditer(r"<item>(.*?)</item>", xml or "", re.S)]


def rss_field(block: str, tag: str) -> str | None:
    m = re.search(
        rf"<{tag}[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{tag}>", block or "", re.S
    )
    return m.group(1).strip() if m else None


def rss_date(block: str) -> datetime | None:
    return parse_rfc822(rss_field(block, "pubDate"))


def rss_categories(block: str, limit: int = 6) -> list[str]:
    return re.findall(
        r"<category>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</category>", block or ""
    )[:limit]


# --------------------------------------------------------------------------
# Atom
#
# Same job as the rss_* family above, different vocabulary: <entry> not
# <item>, ISO-8601 dates not RFC-822, and the permalink in a link element's
# href attribute rather than its text. Static-site generators (Zola, Hugo,
# Jekyll) emit Atom by default, so this is the shape a personal blog usually
# has — see feeds/alperen_keles.py.
# --------------------------------------------------------------------------

_ATOM_LINK_RE = re.compile(r"<link\b([^>]*)>", re.I)


def atom_entries(xml: str) -> list[str]:
    return [m.group(1) for m in
            re.finditer(r"<entry[^>]*>(.*?)</entry>", xml or "", re.S)]


def atom_link(block: str) -> str | None:
    """The permalink, from the href of the entry's alternate link.

    Atom allows several links per entry (alternate, related, enclosure) and
    defines a missing `rel` as meaning "alternate", so a bare `<link href>`
    counts. Anything else is only used as a last resort — better a related
    link than no url at all, since merge.assemble drops url-less items.
    """
    fallback = None
    for m in _ATOM_LINK_RE.finditer(block or ""):
        attrs = m.group(1)
        href = re.search(r'href="([^"]*)"', attrs)
        if not href:
            continue
        rel = re.search(r'rel="([^"]*)"', attrs)
        if rel is None or rel.group(1).lower() == "alternate":
            return html.unescape(href.group(1))
        if fallback is None:
            fallback = html.unescape(href.group(1))
    return fallback


def atom_date(block: str) -> datetime | None:
    """<published> first, <updated> only as a fallback.

    They mean different things: `updated` is the last edit. Preferring it
    would let a years-old post that got a typo fix today land inside the
    freshness window as if it were new.
    """
    return (parse_iso(rss_field(block, "published"))
            or parse_iso(rss_field(block, "updated")))


def atom_author(block: str) -> str | None:
    """<author><name>. Scoped to the author element rather than grabbing the
    first <name> in the entry, which in Atom could belong to a contributor."""
    author = rss_field(block, "author")
    return rss_field(author, "name") if author else None


def atom_categories(block: str, limit: int = 6) -> list[str]:
    """Atom puts the tag in a `term` attribute on a self-closing element
    (`<category term="bliki"/>`), where RSS uses element text — so
    rss_categories silently returns [] on an Atom entry rather than failing.
    Prefers the human-readable `label` when a feed supplies one."""
    out = []
    for attrs in re.findall(r"<category\b([^>]*)>", block or "", re.I):
        m = (re.search(r'label="([^"]*)"', attrs)
             or re.search(r'term="([^"]*)"', attrs))
        if m and m.group(1):
            out.append(html.unescape(m.group(1)))
    return out[:limit]


def atom_content(block: str) -> str:
    """Entry body as HTML, ready for strip_html.

    `<content type="html">` holds HTML that was escaped to survive as XML
    text (`&lt;p&gt;`), so it takes one unescape to become HTML again before
    strip_html can strip it as HTML. Skipping that step doesn't fail loudly —
    it quietly yields text with visible `<p>` tags in it. Doing it here keeps
    the two-step out of every feed module. Falls back to <summary> for feeds
    that publish only a dek.
    """
    raw = rss_field(block, "content") or rss_field(block, "summary") or ""
    return html.unescape(raw)


def strip_html(s: str, limit: int) -> str:
    """Tags first, entities second, whitespace third.

    Tags-before-whitespace: the other order leaves a double space wherever a
    tag was removed - that was a real bug, caught by tests/test_offline.py.

    Entities-after-tags, not before: WordPress feeds (feeds/ken_walger.py) put
    real entities in their text (&#8217;, &#8220;), which would otherwise reach
    the site verbatim. But a post *quoting* markup writes it escaped
    (&lt;b&gt;), and unescaping first would turn that back into a live tag for
    the tag pass to eat - silently deleting the very thing the author was
    showing. Unescaping after means the quoted markup survives as text.
    Whitespace last because unescaping can introduce its own (&nbsp;).
    """
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()[:limit]


def clean_url(url: str) -> str:
    """Drop RSS tracking params."""
    return (url or "").split("?source=")[0].strip()


def slug_words(url: str, limit: int = 20) -> str:
    """Readable words from a URL path — a usable title/label for a link that
    has no anchor text, without carrying the URL itself into any text a
    model might see."""
    tokens = re.split(r"[^a-zA-Z0-9]+", urlparse(url or "").path)
    words = [t for t in tokens if len(t) > 2 and not t.isdigit()]
    return " ".join(words[:limit])


# --------------------------------------------------------------------------
# Item shape
# --------------------------------------------------------------------------

ITEM_FIELDS = (
    "source", "title", "url", "published_at", "author", "tags",
    "description", "body_excerpt",
)


def item(*, source, title, url, published_at, author=None, tags=None,
         description="", body_excerpt=None, **extra) -> dict:
    """Build one normalised item. Every feed module returns these.

    `extra` carries source-specific signals (reactions, score, discussion_url)
    that the digest can use for ranking.

    The title is entity-decoded here rather than in each feed module: it's the
    one field that reaches the site as-is, never passing through strip_html,
    and more than one source ships it escaped (WordPress writes &#8217; for an
    apostrophe, the HN API escapes &quot;). A title is plain text by
    definition, so decoding it is always right.
    """
    url = clean_url(url)
    d = {
        "source": source,
        "title": html.unescape(title or "").strip(),
        "url": url,
        "published_at": published_at,
        "author": author,
        "tags": tags or [],
        "description": description or "",
        "body_excerpt": body_excerpt,
    }
    d.update(extra)
    return d
