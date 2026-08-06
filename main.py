#!/usr/bin/env python3
"""
Daily tech digest — entry point.

    python3 main.py feeds                       list discovered feeds
    python3 main.py fetch [--verbose]           fetch everything -> digest_feed.json
    python3 main.py fetch --only dev_to         fetch one feed
    python3 main.py digest [--dry-run]          full pipeline: fetch, scan newsletters,
                                                 rank+summarize via an LLM (Gemini, falling
                                                 back to Groq/OpenRouter), write the Astro
                                                 site content, update the reading hub
    python3 main.py delete-threads              delete AgentMail threads queued by a prior
                                                 `digest` run — only after that run's output
                                                 has actually been pushed (see cmd_digest step 9)

Adding a source: drop a file in feeds/ exposing fetch(cutoff, **opts).
Removing one: delete the file, or set ENABLED = False in it.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import feeds
import newsletters
import utils
from rank import enrich as rank_enrich
from rank import llm_client
from rank import pools as rank_pools
from rank import prompt as rank_prompt
from rank import relevance as rank_relevance
from rank import summarize as rank_summarize
from rank import write_site_content

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(HERE, "digest_feed.json")
CONFIG_PATH = os.path.join(HERE, "config.json")
HUB_DIR = os.path.join(HERE, "reading-hub")
SITE_DIR = os.path.join(HERE, "site")
PENDING_DELETE_PATH = os.path.join(HERE, ".agentmail_pending_delete.json")

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def load_config() -> dict:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _write_json_atomic(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# --------------------------------------------------------------------------

def cmd_feeds(args) -> int:
    mods = feeds.discover()
    if not mods:
        print("no feeds found in feeds/", file=sys.stderr)
        return 1
    print(f"{len(mods)} feed(s) in {os.path.join(HERE, 'feeds')}:\n")
    for m in mods:
        doc = (m.__doc__ or "").strip().splitlines()
        print(f"  {m.NAME:<22} {doc[0] if doc else ''}")
    print("\nDrop a .py file in feeds/ to add one; delete it to remove one.")
    return 0


def _run_feeds(*, now: datetime, cutoff: datetime, only: list[str] | None,
                no_bodies: bool, verbose: bool) -> dict:
    """Fetch every (or `only` some) feed module. Shared by `fetch` and `digest`."""
    utils.log(f"now={utils.iso(now)} cutoff={utils.iso(cutoff)}", verbose=verbose)

    mods = feeds.discover(only=only)
    per_source: dict[str, list] = {}
    errors: list[str] = []

    def run(mod):
        return mod.fetch(cutoff, verbose=verbose, want_bodies=not no_bodies)

    for mod, result, err in utils.parallel(run, mods, workers=max(1, len(mods))):
        if err:
            per_source[mod.NAME] = []
            errors.append(f"{mod.NAME} crashed: {err}")
            continue
        items, errs = result
        per_source[mod.NAME] = items
        errors.extend(errs or [])

    ordered = []
    for name in sorted(per_source, key=lambda n: -len(per_source[n])):
        ordered.extend(per_source[name])

    total = len(ordered)
    return {
        "generated_at": utils.iso(now),
        "cutoff": utils.iso(cutoff),
        "window_hours": (now - cutoff).total_seconds() / 3600,
        "feeds": sorted(per_source),
        "counts": {k: len(v) for k, v in sorted(per_source.items())},
        "total_items": total,
        "usable_items": total,
        "errors": errors,
        "items": ordered,
    }


def cmd_fetch(args) -> int:
    cfg = load_config()
    hours = args.hours or cfg.get("digest", {}).get("freshness_hours", 24)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours)

    mods_check = feeds.discover(only=args.only)
    if not mods_check:
        print(f"no feeds matched {args.only or '(all)'}", file=sys.stderr)
        return 1

    payload = _run_feeds(now=now, cutoff=cutoff, only=args.only,
                          no_bodies=args.no_bodies, verbose=args.verbose)

    out = os.path.expanduser(args.out)
    try:
        _write_json_atomic(out, payload)
    except OSError as e:
        print(f"could not write {out}: {e}", file=sys.stderr)
        return 2

    counts = "  ".join(f"{k}={v}" for k, v in sorted(payload["counts"].items()))
    print(f"{payload['total_items']} items ({payload['usable_items']} usable) "
          f"-> {out}  {counts}")
    if payload["errors"]:
        print(f"{len(payload['errors'])} error(s):", file=sys.stderr)
        for e in payload["errors"][:10]:
            print(f"  - {e}", file=sys.stderr)
    return 0 if payload["total_items"] else 1


# --------------------------------------------------------------------------

def cmd_pools(args) -> int:
    """Calibration tool: run fetch -> pool 2 -> enrich -> relevance and print
    what would reach pool 3, without ever calling the LLM or writing
    anything. Lets the weights in config.json's `relevance.weights` get
    tuned against real daily data at no API cost."""
    cfg = load_config()
    hours = args.hours or cfg.get("digest", {}).get("freshness_hours", 24)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours)
    verbose = args.verbose

    fetch_payload = _run_feeds(now=now, cutoff=cutoff, only=None, no_bodies=True, verbose=verbose)

    newsletter_items: list[dict] = []
    agentmail_key = os.environ.get("AGENTMAIL_API_KEY")
    agentmail_inbox = os.environ.get("AGENTMAIL_INBOX")
    if agentmail_key and agentmail_inbox:
        result = newsletters.scan(agentmail_inbox, agentmail_key, cutoff, verbose=verbose)
        newsletter_items = result["items"]

    pool2 = rank_pools.build_pool2(fetch_payload["items"], newsletter_items, cutoff, cfg)
    pool2_counts: dict[str, int] = {}
    for item in pool2:
        pool2_counts[item.get("source", "")] = pool2_counts.get(item.get("source", ""), 0) + 1

    # Only items with no description (HN, newsletters) need a body fetch to
    # be scored on comparable text — dev.to/medium/PE already have one,
    # unless relevance.enrich_all_pool2 opts every item in (see PLAN.md D9 —
    # a config edit, not a code change, once the calibration gate decides).
    enrich_all = cfg.get("relevance", {}).get("enrich_all_pool2", False)
    needs_text = pool2 if enrich_all else [i for i in pool2 if not i.get("description")]
    skip_sources = cfg.get("enrich", {}).get("skip_sources", [])
    enrich_errors = rank_enrich.ensure_text(needs_text, skip_sources=skip_sources, verbose=verbose)

    try:
        with open(os.path.join(HUB_DIR, "interests.md"), encoding="utf-8") as f:
            interests_text = f.read()
    except OSError as e:
        print(f"could not read reading-hub/interests.md (is the submodule checked out?): {e}",
              file=sys.stderr)
        return 2

    pool3, dropped = rank_relevance.rank(pool2, interests_text, cfg)

    if args.json:
        payload = {
            "pool2_total": len(pool2),
            "pool2_by_source": pool2_counts,
            "enrich_errors": enrich_errors,
            "dropped": [{"title": i.get("title", ""), "source": i.get("source", ""),
                         **i.get("relevance", {})} for i in dropped],
            "pool3": [{"title": i.get("title", ""), "source": i.get("source", ""),
                       **i.get("relevance", {})} for i in pool3],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(f"pool2: {len(pool2)} item(s)")
    for source in sorted(pool2_counts):
        print(f"  {source:<20} {pool2_counts[source]}")
    if enrich_errors:
        print(f"\nenrich: {len(enrich_errors)} error(s)")
        if verbose:
            for e in enrich_errors[:10]:
                print(f"  - {e}")

    print(f"\nnon-tech drops: {len(dropped)}")
    for item in dropped:
        rel = item.get("relevance", {})
        print(f"  non={rel.get('non_tech', 0):.3f} topic={rel.get('topic_raw', 0):.3f}  "
              f"[{item.get('source', '')}] {item.get('title', '')}")

    print(f"\npool3: {len(pool3)} item(s)")
    for item in pool3:
        rel = item.get("relevance", {})
        print(f"  {rel.get('score', 0):.3f}  topic={rel.get('topic', 0):.3f} "
              f"stack={rel.get('stack', 0):.3f} up={rel.get('dial_up', 0):.3f} "
              f"down={rel.get('dial_down', 0):.3f}  [{item.get('source', '')}] "
              f"{item.get('title', '')}")

    pool3_counts: dict[str, int] = {}
    for item in pool3:
        pool3_counts[item.get("source", "")] = pool3_counts.get(item.get("source", ""), 0) + 1
    print("\npool3 by source: " + "  ".join(f"{k}={v}" for k, v in sorted(pool3_counts.items())))
    return 0


# --------------------------------------------------------------------------

def _compute_target_item_count(reading_pace_log: list[dict], cfg: dict) -> tuple[int, float]:
    """target_item_count = round(target_read_minutes / min_per_item), where
    min_per_item comes from the most recent reading-pace row with both an
    actualReadMin and items value, falling back to config's
    fallback_min_per_item if no row has an actual read time yet."""
    target_minutes = cfg.get("digest", {}).get("target_read_minutes", 30)
    fallback = cfg.get("digest", {}).get("fallback_min_per_item", 3.33)

    candidates = [row for row in reading_pace_log
                  if _DATE_RE.match(row.get("date", "")) and row.get("actualReadMin") and row.get("items")]
    if candidates:
        best = max(candidates, key=lambda r: r["date"])
        min_per_item = best.get("minPerItem") or (best["actualReadMin"] / best["items"])
    else:
        min_per_item = fallback

    return max(1, round(target_minutes / min_per_item)), min_per_item


def _reconcile_digest(digest, pool3: list[dict]) -> dict:
    """Trust only prose (`intro`, section `heading`, item `summary`) from the
    model's digest call. Factual fields — url/title/source/publishedAt/tags —
    are taken from our own pool-3 data, keyed by the integer
    index `i` the model was given (never a url: no candidate carries a url
    into the prompt at all now, which removes an entire class of
    injection — see rank/prompt.py). Non-int, out-of-range, and duplicate
    indices are rejected outright rather than trusted."""
    used_indices: set[int] = set()
    sections_out = []
    for section in (digest.get("sections", []) if isinstance(digest, dict) else []):
        if not isinstance(section, dict):
            continue
        items_out = []
        for item in section.get("items", []) or []:
            if not isinstance(item, dict):
                continue
            idx = item.get("i")
            if not isinstance(idx, int) or isinstance(idx, bool):
                continue
            if idx < 0 or idx >= len(pool3) or idx in used_indices:
                continue
            used_indices.add(idx)
            cand = pool3[idx]
            items_out.append({
                "url": cand["url"],
                "title": cand.get("title", ""),
                "source": cand.get("source", ""),
                "publishedAt": cand.get("published_at", ""),
                "tags": cand.get("tags") or [],
                "summary": item.get("summary", "") if isinstance(item.get("summary"), str) else "",
            })
        if items_out:
            heading = section.get("heading")
            sections_out.append({
                "heading": heading if isinstance(heading, str) and heading else "Reading",
                "items": items_out,
            })
    intro = digest.get("intro", "") if isinstance(digest, dict) else ""
    return {"intro": intro if isinstance(intro, str) else "", "sections": sections_out}


def cmd_digest(args) -> int:
    cfg = load_config()
    hours = args.hours or cfg.get("digest", {}).get("freshness_hours", 24)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours)
    verbose = args.verbose
    errors: list[str] = []

    # 1. Feeds
    fetch_payload = _run_feeds(now=now, cutoff=cutoff, only=None, no_bodies=False, verbose=verbose)
    _write_json_atomic(os.path.expanduser(args.out), fetch_payload)
    errors.extend(fetch_payload["errors"])
    utils.log(f"feeds: {fetch_payload['total_items']} item(s)", verbose=verbose)

    # 2. Newsletters (optional — degrades to feed-only if not configured)
    agentmail_key = os.environ.get("AGENTMAIL_API_KEY")
    agentmail_inbox = os.environ.get("AGENTMAIL_INBOX")
    newsletter_items: list[dict] = []
    thread_ids: list[str] = []
    senders_seen: dict[str, str] = {}
    if agentmail_key and agentmail_inbox:
        result = newsletters.scan(agentmail_inbox, agentmail_key, cutoff, verbose=verbose)
        newsletter_items = result["items"]
        thread_ids = result["thread_ids"]
        senders_seen = result["senders_seen"]
        errors.extend(result["errors"])
        utils.log(f"newsletters: {len(newsletter_items)} item(s) from {len(thread_ids)} thread(s)",
                   verbose=verbose)
    else:
        utils.log("newsletters: AGENTMAIL_API_KEY/AGENTMAIL_INBOX not set, skipping (feed-only)",
                   verbose=True)

    # 3. Pool 2 — deterministic per-source thresholds/caps (rank/pools.py)
    pool2 = rank_pools.build_pool2(fetch_payload["items"], newsletter_items, cutoff, cfg)
    if not pool2:
        print("no qualifying candidates (all stale or empty)", file=sys.stderr)
        return 1
    utils.log(f"pool2: {len(pool2)} item(s)", verbose=verbose)

    # 4. Reading hub
    interests_path = os.path.join(HUB_DIR, "interests.md")
    pace_path = os.path.join(HUB_DIR, "reading-pace.json")
    newsletters_path = os.path.join(HUB_DIR, "newsletters.json")
    try:
        with open(interests_path, encoding="utf-8") as f:
            interests_text = f.read()
        with open(pace_path, encoding="utf-8") as f:
            pace_data = json.load(f)
        with open(newsletters_path, encoding="utf-8") as f:
            newsletters_data = json.load(f)
    except (OSError, ValueError) as e:
        print(f"could not read reading-hub files (is the submodule checked out?): {e}", file=sys.stderr)
        return 2

    if not rank_relevance.parse_interests(interests_text)["topics"]:
        errors.append("interests.md: no '## Topics' bullets found — topic relevance "
                       "scoring and non-tech filtering are disabled. Check the heading.")

    target_count, min_per_item = _compute_target_item_count(pace_data.get("log", []), cfg)
    utils.log(f"target item count: {target_count} (min/item={min_per_item})", verbose=verbose)

    # 5. Enrich pool 2 — only items with no description (HN, newsletters)
    # need a body fetch to be scored on text comparable to the rest, unless
    # relevance.enrich_all_pool2 opts every item in (see PLAN.md D9).
    enrich_all = cfg.get("relevance", {}).get("enrich_all_pool2", False)
    needs_text = pool2 if enrich_all else [i for i in pool2 if not i.get("description")]
    skip_sources = cfg.get("enrich", {}).get("skip_sources", [])
    errors.extend(rank_enrich.ensure_text(needs_text, skip_sources=skip_sources, verbose=verbose))

    # 6. Relevance: pool2 -> pool3 (model2vec vs interests.md)
    pool3, dropped = rank_relevance.rank(pool2, interests_text, cfg)
    utils.log(f"pool3: {len(pool3)} item(s) ({len(dropped)} dropped as non-tech)", verbose=verbose)
    if not pool3:
        print("no candidates survived relevance scoring", file=sys.stderr)
        return 1

    # 7. Fill in remaining bodies for the 25 pool-3 winners (idempotent —
    # reuses whatever step 5 already resolved), then summarize each.
    errors.extend(rank_enrich.ensure_text(pool3, skip_sources=skip_sources, verbose=verbose))
    summarize_cfg = cfg.get("summarize", {})
    for item in pool3:
        text = rank_summarize.pick_text(item)
        item["summary"] = rank_summarize.extractive(
            text, sentences=summarize_cfg.get("sentences", 5),
            max_chars=summarize_cfg.get("max_chars", 900),
            algorithm=summarize_cfg.get("algorithm", "text_rank"))

    # 8. LLM — one call: selection, grouping, and prose together
    if not any(os.environ.get(v) for v in llm_client.PROVIDER_ENV_VARS):
        print(f"no LLM provider configured (set one of: "
              f"{', '.join(llm_client.PROVIDER_ENV_VARS)})", file=sys.stderr)
        return 2

    digest_prompt = rank_prompt.build_digest_prompt(interests_text, pool3, target_count)
    try:
        raw_digest = llm_client.generate_json(digest_prompt, config=cfg)
    except RuntimeError as e:
        print(f"LLM digest call failed: {e}", file=sys.stderr)
        return 2

    digest = _reconcile_digest(raw_digest, pool3)
    if not digest["sections"]:
        print("LLM digest call returned no items matching the candidate list", file=sys.stderr)
        return 2

    # 9. Write site content
    date_str = now.strftime("%Y-%m-%d")
    site_title = cfg.get("site", {}).get("title", "Tech Reading Digest")
    title = f"{site_title} — {now.strftime('%A, %B ')}{now.day}, {now.year}"
    item_count = sum(len(s.get("items", [])) for s in digest.get("sections", []))
    stats = {
        "itemCount": item_count,
        "sourcesScanned": fetch_payload["feeds"] + (["agentmail"] if agentmail_key else []),
        "errors": errors,
    }
    write_path = write_site_content.write_digest(SITE_DIR, date_str, title, utils.iso(now), digest, stats)
    print(f"wrote {write_path} ({item_count} items)")

    # 10. Update reading hub: newsletters registry + reading-pace log
    updated_newsletters, added = newsletters.reconcile_registry(
        newsletters_data.get("newsletters", []), senders_seen, date_str)
    newsletters_data["newsletters"] = updated_newsletters
    flagged: list[str] = []
    if agentmail_key and agentmail_inbox:
        flagged = newsletters.process_unsubscribes(updated_newsletters, agentmail_inbox,
                                                     agentmail_key, verbose=verbose)
    write_site_content.write_hub_file(HUB_DIR, "newsletters.json", newsletters_data)

    note = f"Unattended CI run. {len(added)} new newsletter sender(s)."
    if flagged:
        note += f" {len(flagged)} unsubscribe(s) need a manual click."
    pace_data.setdefault("log", []).append({
        "date": date_str,
        "actualReadMin": None,
        "estReadMin": round(item_count * min_per_item),
        "items": item_count,
        "minPerItem": min_per_item,
        "notes": note,
    })
    write_site_content.write_hub_file(HUB_DIR, "reading-pace.json", pace_data)

    # 11. Queue AgentMail cleanup — do NOT delete here. This process's output
    # (the digest content, the reading-hub updates) isn't actually durable
    # until it's committed and pushed, which happens in a later, separate
    # workflow step that can independently fail. Deleting the source
    # threads now would make that failure mode unrecoverable (the next
    # run's 24h window won't see them again). `main.py delete-threads`
    # reads this file and does the deletion; the workflow only calls it
    # after both pushes succeed.
    if agentmail_key and agentmail_inbox and thread_ids:
        if args.dry_run:
            print(f"dry-run: would queue {len(thread_ids)} AgentMail thread(s) for deletion")
        else:
            _write_json_atomic(PENDING_DELETE_PATH,
                                {"inbox": agentmail_inbox, "thread_ids": thread_ids})
            print(f"queued {len(thread_ids)} AgentMail thread(s) for deletion "
                  f"after a successful push (run `main.py delete-threads`)")

    if flagged:
        print(f"{len(flagged)} unsubscribe(s) need your attention:")
        for note in flagged:
            print(f"  - {note}")
    if errors:
        print(f"{len(errors)} error(s) during this run:", file=sys.stderr)
        for e in errors[:10]:
            print(f"  - {e}", file=sys.stderr)

    return 0


def cmd_delete_threads(args) -> int:
    """Delete AgentMail threads queued by a prior `digest` run. Intended to
    run as a separate, later workflow step, only after that run's output
    has actually been pushed successfully — see the comment at step 9 in
    cmd_digest for why this is split out."""
    try:
        with open(PENDING_DELETE_PATH, encoding="utf-8") as f:
            queued = json.load(f)
    except FileNotFoundError:
        print("nothing queued for deletion")
        return 0
    except (OSError, ValueError) as e:
        print(f"could not read {PENDING_DELETE_PATH}: {e}", file=sys.stderr)
        return 2

    api_key = os.environ.get("AGENTMAIL_API_KEY")
    if not api_key:
        print("AGENTMAIL_API_KEY not set", file=sys.stderr)
        return 2

    thread_ids = queued.get("thread_ids", [])
    errors = newsletters.delete_used_threads(queued["inbox"], thread_ids, api_key,
                                              verbose=args.verbose)
    os.remove(PENDING_DELETE_PATH)
    print(f"deleted {len(thread_ids) - len(errors)}/{len(thread_ids)} queued thread(s)")
    if errors:
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    return 0


# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(prog="main.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("feeds", help="list discovered feed modules").set_defaults(
        func=cmd_feeds)

    f = sub.add_parser("fetch", help="fetch all feeds into digest_feed.json")
    f.add_argument("--out", default=DEFAULT_OUT)
    f.add_argument("--hours", type=int, help="freshness window (default from config)")
    f.add_argument("--only", nargs="+", metavar="FEED", help="only these feeds")
    f.add_argument("--no-bodies", action="store_true",
                   help="skip article body fetch - faster, thinner summaries")
    f.add_argument("--verbose", "-v", action="store_true")
    f.set_defaults(func=cmd_fetch)

    d = sub.add_parser("digest", help="full pipeline: fetch, newsletters, rank+summarize, write site content")
    d.add_argument("--out", default=DEFAULT_OUT, help="where to also write the raw feed fetch")
    d.add_argument("--hours", type=int, help="freshness window (default from config)")
    d.add_argument("--dry-run", action="store_true",
                   help="run the full pipeline and write output locally, but don't queue "
                        "AgentMail threads for deletion (the workflow, not this command, "
                        "decides whether to push, and cleanup only runs after a push succeeds)")
    d.add_argument("--verbose", "-v", action="store_true")
    d.set_defaults(func=cmd_digest)

    dt = sub.add_parser("delete-threads",
                         help="delete AgentMail threads queued by a prior `digest` run "
                              "(run only after that run's output has been pushed)")
    dt.add_argument("--verbose", "-v", action="store_true")
    dt.set_defaults(func=cmd_delete_threads)

    p = sub.add_parser("pools", help="calibration tool: fetch -> pool2 -> enrich -> relevance, "
                                      "print what would reach pool 3 (no LLM call, no writes)")
    p.add_argument("--hours", type=int, help="freshness window (default from config)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--verbose", "-v", action="store_true")
    p.set_defaults(func=cmd_pools)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
