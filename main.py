#!/usr/bin/env python3
"""
Daily tech digest — entry point.

    python3 main.py feeds                       list discovered feeds
    python3 main.py fetch [--verbose]           fetch everything -> digest_feed.json
    python3 main.py fetch --only dev_to         fetch one feed
    python3 main.py publish --html d.html --title "..."   Telegraph, then Instapaper

Adding a source: drop a file in feeds/ exposing fetch(cutoff, **opts).
Removing one: delete the file, or set ENABLED = False in it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import feeds
import publish as publisher
import utils

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(HERE, "digest_feed.json")
CONFIG_PATH = os.path.join(HERE, "config.json")


def load_config() -> dict:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


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


def cmd_fetch(args) -> int:
    cfg = load_config()
    hours = args.hours or cfg.get("digest", {}).get("freshness_hours", 24)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours)
    utils.log(f"now={utils.iso(now)} cutoff={utils.iso(cutoff)}", verbose=args.verbose)

    mods = feeds.discover(only=args.only)
    if not mods:
        print(f"no feeds matched {args.only or '(all)'}", file=sys.stderr)
        return 1

    per_source: dict[str, list] = {}
    errors: list[str] = []

    # Feeds run concurrently with each other, and most parallelise internally.
    def run(mod):
        return mod.fetch(cutoff, verbose=args.verbose,
                         want_bodies=not args.no_bodies)

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

    payload = {
        "generated_at": utils.iso(now),
        "cutoff": utils.iso(cutoff),
        "window_hours": hours,
        "feeds": sorted(per_source),
        "counts": {k: len(v) for k, v in sorted(per_source.items())},
        "total_items": total,
        "usable_items": usable,
        "errors": errors,
        "items": ordered,
    }

    out = os.path.expanduser(args.out)
    try:
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        tmp = out + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, out)  # atomic: a reader never sees a half-written file
    except OSError as e:
        print(f"could not write {out}: {e}", file=sys.stderr)
        return 2

    counts = "  ".join(f"{k}={len(v)}" for k, v in sorted(per_source.items()))
    print(f"{total} items ({usable} not paywall-flagged) -> {out}  {counts}")
    if errors:
        print(f"{len(errors)} error(s):", file=sys.stderr)
        for e in errors[:10]:
            print(f"  - {e}", file=sys.stderr)
    return 0 if total else 1


def cmd_publish(args) -> int:
    return publisher.run(
        html_path=args.html,
        title=args.title,
        author=args.author,
        account=args.account,
        dry_run=args.dry_run,
        skip_instapaper=args.no_instapaper,
        config=load_config(),
    )


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

    p = sub.add_parser("publish", help="publish to Telegraph, then Instapaper")
    p.add_argument("--html", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--author", help="Telegraph byline (default from config)")
    p.add_argument("--account", help="Instapaper account (default from config)")
    p.add_argument("--dry-run", action="store_true",
                   help="print converted nodes, publish nothing")
    p.add_argument("--no-instapaper", action="store_true")
    p.set_defaults(func=cmd_publish)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
