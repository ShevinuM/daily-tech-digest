"""Deterministic newsletter classification: content vs non-content, candidate
article-link extraction, and original-publish-date verification.

The judgement calls (which links are worth reading, how to summarize them)
are left to the ranking model — this module only does the mechanical, no-AI
work: is this thread worth looking at at all, what links does it contain,
and can we prove when the linked article was actually published (newsletters
routinely resurface 1-2 day old stories, which fail the fresh-only rule).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

NON_CONTENT_SUBJECT_HINTS = (
    "welcome", "confirm your subscription", "please confirm",
    "verify your email", "verification code", "one-time passcode", "otp",
    "your login code", "sign-in code", "receipt", "payment confirmation",
    "you've been unsubscribed", "you have been unsubscribed",
)

# Hostnames that are never the actual article, however often they appear in
# a newsletter body: tracking pixels, unsubscribe/preference links, social
# share links, mailto, the sender's own footer/logo links.
NON_ARTICLE_HOST_HINTS = (
    "unsubscribe", "list-manage", "click.", "trk.", "/track/", "mailto:",
    "facebook.com/sharer", "twitter.com/intent", "x.com/intent",
    "linkedin.com/sharing", "linkedin.com/shareArticle",
    "t.co/", "utm_source=email-share",
)

TWITTER_EPOCH_MS = 1288834974657


def is_non_content(subject: str, body_text: str) -> bool:
    """True for welcome/confirm/OTP/receipt/marketing noise, not a real issue."""
    s = (subject or "").lower()
    if any(hint in s for hint in NON_CONTENT_SUBJECT_HINTS):
        return True
    text = body_text or ""
    if len(text.strip()) < 80 and "http" not in text:
        return True
    return False


_URL_DATE_RE = re.compile(r"/(20\d{2})[/-](\d{2})[/-](\d{2})(?:[/-]|$)")


def date_from_url(url: str) -> datetime | None:
    """Publish date embedded in a URL path, e.g. /2026/08/03/some-post."""
    m = _URL_DATE_RE.search(url)
    if not m:
        return None
    year, month, day = (int(x) for x in m.groups())
    try:
        return datetime(year, month, day, tzinfo=timezone.utc)
    except ValueError:
        return None


_TWEET_STATUS_RE = re.compile(r"(?:twitter\.com|x\.com)/\w+/status/(\d+)")


def date_from_snowflake(url: str) -> datetime | None:
    """Decode an X/Twitter snowflake status ID into its creation timestamp."""
    m = _TWEET_STATUS_RE.search(url)
    if not m:
        return None
    tweet_id = int(m.group(1))
    ts_ms = (tweet_id >> 22) + TWITTER_EPOCH_MS
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)


def verify_published_at(url: str) -> datetime | None:
    """Best-effort original publish date for a linked article. None if it
    can't be established from the URL alone — callers must drop the item
    rather than guess or fall back to the newsletter's send date."""
    return date_from_url(url) or date_from_snowflake(url)


_ANCHOR_RE = re.compile(
    r'<a\b[^>]*\bhref=["\'](?P<url>https?://[^"\']+)["\'][^>]*>(?P<text>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")


def extract_links(html: str, text: str) -> list[dict]:
    """Candidate {url, anchor_text} pairs from a newsletter body. HTML is
    preferred (it carries anchor text); falls back to bare URLs in the
    plain-text body. Obvious non-article links (unsubscribe, tracking,
    social-share, mailto) are filtered out; everything else is left for the
    ranking step to judge."""
    seen: set[str] = set()
    out: list[dict] = []

    if html:
        for m in _ANCHOR_RE.finditer(html):
            url = m.group("url").rstrip(").,>")
            if _is_non_article(url) or url in seen:
                continue
            anchor_text = _TAG_RE.sub(" ", m.group("text"))
            anchor_text = " ".join(anchor_text.split())
            seen.add(url)
            out.append({"url": url, "anchor_text": anchor_text})
    elif text:
        for raw in re.findall(r"https?://\S+", text):
            url = raw.rstrip(").,>")
            if _is_non_article(url) or url in seen:
                continue
            seen.add(url)
            out.append({"url": url, "anchor_text": ""})

    return out


def _is_non_article(url: str) -> bool:
    lowered = url.lower()
    return any(hint in lowered for hint in NON_ARTICLE_HOST_HINTS)
