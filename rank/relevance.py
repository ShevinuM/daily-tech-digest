"""Pool 2 -> pool 3: score every item against reading-hub/interests.md with
model2vec sentence embeddings, drop items that read as non-tech, then take
the top `pool3.size` with a per-source cap. Replaces the LLM's old
freshness/tech-only/topic-relevance judgement with something cheap, fast,
and comparable across sources — see PLAN.md Findings 1-4.

One exception: items from `pools.priority_sources` (blogs read directly
rather than discovered) are pinned. They're still scored, but neither the
non-tech drop nor the top-N cut applies to them, and they don't consume a
`pool3.size` slot. Every item leaves here carrying a boolean `priority`.
"""
from __future__ import annotations

import re

import numpy as np

import utils

PRIORITY_WEIGHTS = {"high": 1.0, "medium": 0.7, "low": 0.4}
DEFAULT_TOPIC_WEIGHT = 1.0

# A module constant, not read from interests.md: these anchors exist purely
# to detect "this item isn't about tech at all", which is a different axis
# than topic priority and shouldn't be user-tunable per bullet.
NON_TECH_ANCHORS = [
    "politics, elections, government, policy, war, protests",
    "sports, football, basketball, match results, athletes",
    "celebrity, entertainment, movies, music, gossip, television",
    "recipes, cooking, travel, lifestyle, fashion, health and fitness",
    "crime, courts, lawsuits, obituaries, weather, natural disasters",
]

_SECTION_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", re.M)
_BULLET_RE = re.compile(r"^[-*]\s+(.+)$", re.M)
# Swallows the marker's surrounding punctuation too (— *High*.), so removing
# it leaves clean query text rather than a stray dash/period.
_PRIORITY_MARKER_RE = re.compile(r"\s*[—–-]?\s*\*(High|Medium|Low)\*\.?", re.I)
_EMPHASIS_RE = re.compile(r"\*\*|\*|__|_")

_MODEL_CACHE: dict[str, object] = {}


# --------------------------------------------------------------------------
# interests.md parsing
# --------------------------------------------------------------------------

def _strip_emphasis(text: str) -> str:
    return _EMPHASIS_RE.sub("", text).strip()


def _split_sections(text: str) -> dict[str, str]:
    """Map each `## Heading` (lowercased) to the text below it, up to the
    next heading."""
    sections: dict[str, str] = {}
    current = None
    buf: list[str] = []
    for line in (text or "").splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            if current is not None:
                sections[current] = "\n".join(buf)
            current = m.group(1).strip().lower()
            buf = []
        elif current is not None:
            buf.append(line)
    if current is not None:
        sections[current] = "\n".join(buf)
    return sections


def _bullets(body: str) -> list[str]:
    return [m.group(1).strip() for m in _BULLET_RE.finditer(body or "")]


# Headings whose bullets become dial-down anchors. "Dial down" and "Exclude"
# are the same signal written at two strengths — both name things the reader
# wants less of — so both feed the one term the scorer has for it. Read as a
# union, not a fallback: a file can use either heading or both, and adding one
# never silently disables the other.
#
# This exists because the split was a silent failure mode. The hub's
# interests.md replaced "## Dial down" with "## Exclude" on 2026-09-10; nothing
# errored, but dial_down parsed to [] for a week, `dial_down_sim` was 0.0 for
# every item, and the 0.60 weight in config.json did nothing — while the file
# said in plain English which categories to keep out.
_DIAL_DOWN_HEADINGS = ("dial down", "exclude")


def parse_interests(text: str) -> dict:
    """Sections are matched case-insensitively; the live file uses lowercase
    `## My stack`. A missing section yields an empty list, never a crash.

    Note the asymmetry between what `## Exclude` says and what it does: the
    file reads "Do not include these", but these anchors only subtract
    `weights.dial_down` from an item's score — they demote, they don't drop.
    A hard exclusion would need its own rule.
    """
    sections = _split_sections(text)

    topics = []
    for bullet in _bullets(sections.get("topics", "")):
        marker = _PRIORITY_MARKER_RE.search(bullet)
        weight = PRIORITY_WEIGHTS[marker.group(1).lower()] if marker else DEFAULT_TOPIC_WEIGHT
        query = _PRIORITY_MARKER_RE.sub("", bullet)  # weight is metadata, not query text
        topics.append((_strip_emphasis(query), weight))

    dial_down = [b for heading in _DIAL_DOWN_HEADINGS
                 for b in _bullets(sections.get(heading, ""))]

    return {
        "topics": topics,
        "stack": [_strip_emphasis(b) for b in _bullets(sections.get("my stack", ""))],
        "dial_up": [_strip_emphasis(b) for b in _bullets(sections.get("dial up", ""))],
        "dial_down": [_strip_emphasis(b) for b in dial_down],
    }


# --------------------------------------------------------------------------
# document text
# --------------------------------------------------------------------------

DOC_BODY_CHARS = 600  # override via relevance.doc_body_chars


def _url_slug_words(url: str, limit: int = 20) -> str:
    return utils.slug_words(url, limit=limit)


def _doc_text(item: dict, body_chars: int = DOC_BODY_CHARS) -> str:
    """Whichever of `description`/`article_text` actually carries more
    signal, under one shared budget: a ~90-char RSS blurb must not out-rank
    a 5k-char extracted article on length, and (in the other direction) an
    extracted article must not out-rank a blurb purely because it's longer
    — see PLAN.md D9. Preferring `description` unconditionally (the
    original v1 behaviour) meant dev.to/medium items were scored on ~110
    chars while enriched items got 600, even when a full article_text was
    sitting on the item unused."""
    title = item.get("title") or ""
    tags = " ".join(item.get("tags") or [])
    desc = (item.get("description") or "").strip()
    body = (item.get("article_text") or "").strip()
    chosen = body if len(body) > len(desc) else desc
    parts = [p for p in (title, tags, chosen[:body_chars]) if p]
    if parts:
        return " — ".join(parts)
    return _url_slug_words(item.get("url", ""))


# --------------------------------------------------------------------------
# embedding model
# --------------------------------------------------------------------------

def _default_encode(model_name: str):
    def encode(texts: list[str]):
        model = _MODEL_CACHE.get(model_name)
        if model is None:
            from model2vec import StaticModel
            model = StaticModel.from_pretrained(model_name)
            _MODEL_CACHE[model_name] = model
        return model.encode(texts)
    return encode


def _l2_normalize(mat: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return mat / norms


def _max_sim(doc_vec: np.ndarray, anchor_vecs: np.ndarray) -> float:
    if anchor_vecs.size == 0:
        return 0.0
    return float((doc_vec @ anchor_vecs.T).max())


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------

def _score_and_select(items, parsed, vectors, n_docs, *, w_stack, w_dial_up, w_dial_down,
                       drop_non_tech, never_drop_sources, size, max_per_source,
                       priority_sources=()):
    """Pure numpy/dict work — scoring, the non-tech drop, sorting, and the
    per-source cap. Deliberately unguarded: unlike the encode() call in
    rank(), a bug here is a real bug and should crash loudly rather than be
    mistaken for "the embedding model is unavailable" (see D6 in PLAN.md)."""
    topic_texts = [t for t, _ in parsed["topics"]]
    topic_weights = np.asarray([w for _, w in parsed["topics"]], dtype=float)
    stack_texts = parsed["stack"]
    dial_up_texts = parsed["dial_up"]
    dial_down_texts = parsed["dial_down"]

    doc_vecs = vectors[:n_docs]
    idx = n_docs
    topic_vecs = vectors[idx:idx + len(topic_texts)]; idx += len(topic_texts)
    stack_vecs = vectors[idx:idx + len(stack_texts)]; idx += len(stack_texts)
    dial_up_vecs = vectors[idx:idx + len(dial_up_texts)]; idx += len(dial_up_texts)
    dial_down_vecs = vectors[idx:idx + len(dial_down_texts)]; idx += len(dial_down_texts)
    non_tech_vecs = vectors[idx:idx + len(NON_TECH_ANCHORS)]; idx += len(NON_TECH_ANCHORS)

    priority_sources = set(priority_sources or ())

    scored: list[dict] = []
    dropped: list[dict] = []
    for i, item in enumerate(items):
        doc_vec = doc_vecs[i]
        item["priority"] = item.get("source", "") in priority_sources

        if topic_vecs.size:
            topic_sims = doc_vec @ topic_vecs.T
            raw_topic_sim = float(topic_sims.max())
            weighted_topic_sim = float((topic_sims * topic_weights).max())
        else:
            raw_topic_sim = 0.0
            weighted_topic_sim = 0.0

        stack_sim = _max_sim(doc_vec, stack_vecs)
        dial_up_sim = _max_sim(doc_vec, dial_up_vecs)
        dial_down_sim = _max_sim(doc_vec, dial_down_vecs)
        non_tech_sim = _max_sim(doc_vec, non_tech_vecs)

        score = (weighted_topic_sim + w_stack * stack_sim
                 + w_dial_up * dial_up_sim - w_dial_down * dial_down_sim)
        item["relevance"] = {
            "score": score, "topic": weighted_topic_sim, "topic_raw": raw_topic_sim,
            "stack": stack_sim, "dial_up": dial_up_sim, "dial_down": dial_down_sim,
            "non_tech": non_tech_sim,
        }

        if (drop_non_tech and item.get("source") not in never_drop_sources
                and non_tech_sim > raw_topic_sim):
            dropped.append(item)
            utils.log(f"relevance: dropped (non-tech) non={non_tech_sim:.3f} "
                      f"topic={raw_topic_sim:.3f}  {item.get('title', '')}", verbose=True)
            continue

        scored.append(item)

    scored.sort(key=lambda it: -it["relevance"]["score"])

    # Priority-source items are pinned: they skip the top-`size` cut and the
    # per-source cap entirely, and sit ahead of the merit-ranked rest. They
    # deliberately do NOT consume a `size` slot either — the point of the tier
    # is that a blog you read directly never displaces the 25-deep merit pool,
    # and vice versa. They're still scored above (main.py pools reports it,
    # and the score orders them among themselves), just not filtered on.
    pinned = [it for it in scored if it.get("priority")]
    rest = [it for it in scored if not it.get("priority")]

    selected: list[dict] = []
    overflow: list[dict] = []
    counts: dict[str, int] = {}
    for item in rest:
        if len(selected) >= size:
            break
        cap = max_per_source.get(item.get("source", ""))
        if cap is not None and counts.get(item.get("source", ""), 0) >= cap:
            overflow.append(item)
            continue
        selected.append(item)
        counts[item.get("source", "")] = counts.get(item.get("source", ""), 0) + 1

    for item in overflow:
        if len(selected) >= size:
            break
        selected.append(item)

    return pinned + selected, dropped


def _fallback_rank(items: list[dict], size: int, max_per_source: dict | None = None,
                    priority_sources=()) -> list[dict]:
    """Deterministic ordering used when the embedding model can't be
    loaded: interleave sources round-robin in their existing engagement
    order (`pool2_rank`, already sorted by reactions/score for dev.to/HN in
    rank/pools.py), honouring the same per-source caps as the scored path
    (caps redistribute, never shrink) — without them, a fallback run is
    exactly the situation where a single noisy source running away with the
    digest is most likely. Every returned item carries a `relevance` key
    (score 0.0, `fallback: True`) so callers/`main.py pools` can tell a
    fallback ranking apart from a real score of zero.

    Priority sources are pinned here exactly as in the scored path — without
    this, a cold model cache in CI would silently demote the whole tier back
    into the round-robin and quietly drop it on a busy day."""
    max_per_source = max_per_source or {}
    priority_sources = set(priority_sources or ())

    for item in items:
        item["priority"] = item.get("source", "") in priority_sources
    pinned = [it for it in items if it["priority"]]
    items = [it for it in items if not it["priority"]]

    groups: dict[str, list[dict]] = {}
    for item in items:
        groups.setdefault(item.get("source", ""), []).append(item)
    for group in groups.values():
        group.sort(key=lambda it: it.get("pool2_rank", 0))

    keys = sorted(groups)
    picked: list[dict] = []
    overflow: list[dict] = []
    counts: dict[str, int] = {}
    depth = 0
    while len(picked) < size:
        added = False
        for key in keys:
            group = groups[key]
            if depth >= len(group):
                continue
            added = True
            item = group[depth]
            cap = max_per_source.get(key)
            if cap is not None and counts.get(key, 0) >= cap:
                overflow.append(item)
                continue
            if len(picked) >= size:
                break
            picked.append(item)
            counts[key] = counts.get(key, 0) + 1
        if not added:
            break
        depth += 1

    for item in overflow:
        if len(picked) >= size:
            break
        picked.append(item)

    pinned.sort(key=lambda it: it.get("pool2_rank", 0))
    picked = pinned + picked

    for item in picked:
        item.setdefault("relevance", {"score": 0.0, "fallback": True})
    return picked


def rank(items: list[dict], interests_text: str, cfg: dict, *, encode=None
          ) -> tuple[list[dict], list[dict]]:
    """Returns (pool3, dropped). `encode` is an injection point for tests —
    a callable `texts -> array-like of vectors` — so nothing here downloads
    a model unless the caller lets the default lazily load one."""
    relevance_cfg = cfg.get("relevance", {})
    model_name = relevance_cfg.get("model", "minishlab/potion-retrieval-32M")
    weights = relevance_cfg.get("weights", {})
    w_stack = weights.get("stack", 0.30)
    w_dial_up = weights.get("dial_up", 0.35)
    w_dial_down = weights.get("dial_down", 0.60)
    drop_non_tech = relevance_cfg.get("drop_non_tech", True)
    doc_body_chars = relevance_cfg.get("doc_body_chars", DOC_BODY_CHARS)
    pools_cfg = cfg.get("pools", {})
    pool3_cfg = pools_cfg.get("pool3", {})
    size = pool3_cfg.get("size", 25)
    max_per_source = pool3_cfg.get("max_per_source") or {}
    # A priority source is never-drop by definition — one config list, not two.
    # `never_drop_sources` keeps its narrower meaning for anything you want
    # exempt from the non-tech drop but still ranked on merit.
    priority_sources = set(pools_cfg.get("priority_sources") or [])
    never_drop_sources = set(relevance_cfg.get("never_drop_sources") or []) | priority_sources

    parsed = parse_interests(interests_text)
    active_encode = encode or _default_encode(model_name)

    doc_texts = [_doc_text(it, doc_body_chars) for it in items]
    topic_texts = [t for t, _ in parsed["topics"]]
    anchor_texts = (topic_texts + parsed["stack"] + parsed["dial_up"]
                    + parsed["dial_down"] + NON_TECH_ANCHORS)

    if drop_non_tech and not topic_texts:
        utils.log("relevance: no parsable '## Topics' bullets in interests.md — "
                  "disabling the non-tech drop (with no topic anchors, raw_topic_sim "
                  "is 0.0 for everything, and the drop rule would fire on the whole "
                  "pool)", verbose=True)
        drop_non_tech = False

    try:
        raw_vectors = active_encode(doc_texts + anchor_texts)
    except Exception as e:  # noqa: BLE001 - HF Hub down / cold cache / no network.
        # Deliberately broad, and deliberately narrow in scope: this covers
        # only the model call itself. Converting its output to a numpy array
        # and everything in _score_and_select is pure numpy/dict work — a
        # malformed encoder output or a bug in the scoring arithmetic there
        # must crash loudly, not be swallowed as "the model is unavailable".
        utils.log(f"relevance: embedding scoring unavailable ({e}); falling back to "
                  f"deterministic engagement-sorted ordering", verbose=True)
        return _fallback_rank(items, size, max_per_source, priority_sources), []

    vectors = _l2_normalize(np.asarray(raw_vectors, dtype=float))

    return _score_and_select(
        items, parsed, vectors, len(doc_texts), w_stack=w_stack, w_dial_up=w_dial_up,
        w_dial_down=w_dial_down, drop_non_tech=drop_non_tech,
        never_drop_sources=never_drop_sources, size=size, max_per_source=max_per_source,
        priority_sources=priority_sources)
