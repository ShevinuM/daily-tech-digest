"""Write the finished digest to the Astro content collection, and persist
updated reading-hub files (newsletters registry, reading-pace log) so the
caller can commit them. Same atomic-write pattern as main.py's fetch output.
"""
from __future__ import annotations

import json
import os


def _atomic_write_json(path: str, payload: dict) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def write_digest(site_dir: str, date: str, title: str, generated_at: str,
                  digest: dict, stats: dict) -> str:
    """Write site/src/content/digests/<date>.json. Returns the path written."""
    path = os.path.join(site_dir, "src", "content", "digests", f"{date}.json")
    payload = {
        "date": date,
        "title": title,
        "generatedAt": generated_at,
        "intro": digest.get("intro", ""),
        "sections": digest.get("sections", []),
        "stats": stats,
    }
    return _atomic_write_json(path, payload)


def write_hub_file(hub_dir: str, filename: str, payload: dict) -> str:
    """Write an updated reading-hub JSON file (newsletters.json,
    reading-pace.json)."""
    return _atomic_write_json(os.path.join(hub_dir, filename), payload)
