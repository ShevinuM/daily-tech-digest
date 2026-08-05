#!/usr/bin/env python3
"""
Daily tech digest — entry point.

    python3 main.py feeds                       list discovered feeds
    python3 main.py fetch [--verbose]           fetch everything -> digest_feed.json
    python3 main.py fetch --only dev_to         fetch one feed
    python3 main.py digest [--dry-run]          full pipeline: fetch, scan newsletters,
                                                 rank+summarize via Gemini, write the Astro
                                                 site content, update the reading hub

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
from rank import gemini_client
from rank import merge as rank_merge
from rank import prompt as rank_prompt
from rank import write_site_content

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(HERE, "digest_feed.json")
CONFIG_PATH = os.path.join(HERE, "config.json")
HUB_DIR = os.path.join(HERE, "reading-hub")
SITE_DIR = os.path.join(HERE, "site")

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
    usable = sum(1 for i in ordered if not i.get("paywalled"))

    return {
        "generated_at": utils.iso(now),
        "cutoff": utils.iso(cutoff),
        "window_hours": (now - cutoff).total_seconds() / 3600,
        "feeds": sorted(per_source),
        "counts": {k: len(v) for k, v in sorted(per_source.items())},
        "total_items": total,
        "usable_items": usable,
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
    print(f"{payload['total_items']} items ({payload['usable_items']} not paywall-flagged) "
          f"-> {out}  {counts}")
    if payload["errors"]:
        print(f"{len(payload['errors'])} error(s):", file=sys.stderr)
        for e in payload["errors"][:10]:
            print(f"  - {e}", file=sys.stderr)
    return 0 if payload["total_items"] else 1


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

    # 3. Merge + qualify (dedupe, drop paywalled/stale)
    candidates = rank_merge.assemble(fetch_payload["items"], newsletter_items, cutoff)
    if not candidates:
        print("no qualifying candidates (all stale, paywalled, or empty)", file=sys.stderr)
        return 1

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

    target_count, min_per_item = _compute_target_item_count(pace_data.get("log", []), cfg)
    utils.log(f"target item count: {target_count} (min/item={min_per_item})", verbose=verbose)

    # 5. Gemini: selection
    gemini_key = os.environ.get("GEMINI_API_KEY")
    if not gemini_key:
        print("GEMINI_API_KEY not set", file=sys.stderr)
        return 2
    model = cfg.get("gemini", {}).get("model", "gemini-2.5-flash")

    trimmed = rank_merge.trim_for_selection(candidates)
    selection_prompt = rank_prompt.build_selection_prompt(interests_text, trimmed, target_count)
    try:
        selection = gemini_client.generate_json(selection_prompt, api_key=gemini_key, model=model)
    except RuntimeError as e:
        print(f"Gemini selection call failed: {e}", file=sys.stderr)
        return 2

    by_url = {c["url"]: c for c in candidates}
    grouped: dict[str, list[dict]] = {}
    order: list[str] = []
    for pick in selection if isinstance(selection, list) else []:
        cand = by_url.get(pick.get("url"))
        if not cand:
            continue
        section = pick.get("section") or "Reading"
        if section not in grouped:
            grouped[section] = []
            order.append(section)
        grouped[section].append(cand)

    if not grouped:
        print("Gemini selection returned no items matching the candidate list", file=sys.stderr)
        return 2

    # 6. Gemini: summary
    grouped_payload = [{"section": s, "items": grouped[s]} for s in order]
    try:
        digest = gemini_client.generate_json(rank_prompt.build_summary_prompt(grouped_payload),
                                              api_key=gemini_key, model=model)
    except RuntimeError as e:
        print(f"Gemini summary call failed: {e}", file=sys.stderr)
        return 2

    # 7. Write site content
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

    # 8. Update reading hub: newsletters registry + reading-pace log
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

    # 9. Clean up AgentMail — only real runs, only threads actually used
    if agentmail_key and agentmail_inbox and thread_ids:
        if args.dry_run:
            print(f"dry-run: would delete {len(thread_ids)} AgentMail thread(s)")
        else:
            errors.extend(newsletters.delete_used_threads(agentmail_inbox, thread_ids,
                                                            agentmail_key, verbose=verbose))

    if flagged:
        print(f"{len(flagged)} unsubscribe(s) need your attention:")
        for note in flagged:
            print(f"  - {note}")
    if errors:
        print(f"{len(errors)} error(s) during this run:", file=sys.stderr)
        for e in errors[:10]:
            print(f"  - {e}", file=sys.stderr)

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
                   help="run the full pipeline and write output locally, but don't delete "
                        "AgentMail threads (the workflow, not this command, decides whether to push)")
    d.add_argument("--verbose", "-v", action="store_true")
    d.set_defaults(func=cmd_digest)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
