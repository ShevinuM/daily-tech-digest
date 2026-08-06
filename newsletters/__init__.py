"""Newsletter scanning: AgentMail -> classified, date-verified candidate items.

Orchestrates agentmail_client + classify + unsubscribe into the calls
main.py's `digest` subcommand needs. Produces items shaped like
utils.item(), so rank/merge.py can treat feed and newsletter candidates
identically.
"""
from __future__ import annotations

from datetime import datetime, timezone

import utils

from . import agentmail_client as client
from . import classify
from . import unsubscribe as unsub


def scan(inbox_id: str, api_key: str, cutoff: datetime, *, verbose: bool = False) -> dict:
    """Fetch, classify, and date-verify newsletter items since `cutoff`.

    Returns {"items": [...], "thread_ids": [...], "senders_seen": {sender: iso_date},
    "errors": [...]}. `thread_ids` are the CONTENT threads actually used for
    a kept item — callers should only delete these, and only after a
    successful publish.
    """
    items: list[dict] = []
    thread_ids: list[str] = []
    senders_seen: dict[str, str] = {}
    errors: list[str] = []

    try:
        messages = client.list_messages(inbox_id, api_key, after=utils.iso(cutoff))
    except RuntimeError as e:
        return {"items": [], "thread_ids": [], "senders_seen": {}, "errors": [str(e)]}

    now = datetime.now(timezone.utc)
    seen_threads: set[str] = set()
    for msg in messages:
        thread_id = msg.get("thread_id")
        if not thread_id or thread_id in seen_threads:
            continue
        seen_threads.add(thread_id)

        sender = msg.get("from", "")
        timestamp = msg.get("timestamp") or msg.get("created_at") or ""
        if sender and timestamp and timestamp > senders_seen.get(sender, ""):
            senders_seen[sender] = timestamp

        try:
            thread = client.get_thread(inbox_id, thread_id, api_key)
        except RuntimeError as e:
            errors.append(f"get_thread {thread_id} crashed: {e}")
            continue

        thread_messages = thread.get("messages") or [msg]
        subject = thread.get("subject") or msg.get("subject", "")
        combined_text = "\n".join(m.get("text", "") for m in thread_messages)
        combined_html = "\n".join(m.get("html", "") for m in thread_messages)

        if classify.is_non_content(subject, combined_text):
            utils.log(f"newsletter: skip non-content thread {thread_id!r} ({subject!r})",
                       verbose=verbose)
            continue

        kept_any = False
        for link in classify.extract_links(combined_html, combined_text):
            published_at = classify.verify_published_at(link["url"])
            if not published_at or published_at < cutoff or published_at > now:
                continue
            items.append(utils.item(
                source=f"newsletter:{sender or 'unknown'}",
                title=link["anchor_text"] or utils.slug_words(link["url"]) or "Untitled link",
                url=link["url"],
                published_at=utils.iso(published_at),
                author=sender,
                description="",
            ))
            kept_any = True

        if kept_any:
            thread_ids.append(thread_id)
        utils.log(f"newsletter: {subject!r} from {sender!r} -> "
                   f"{'kept' if kept_any else 'no dated links'}", verbose=verbose)

    return {"items": items, "thread_ids": thread_ids, "senders_seen": senders_seen,
            "errors": errors}


def reconcile_registry(newsletters: list[dict], senders_seen: dict[str, str],
                        today: str) -> tuple[list[dict], list[str]]:
    """Bump lastSeen for senders that emailed today; append a default entry
    for any new sender. Returns (updated_registry, newly_added_senders)."""
    by_sender = {n["sender"]: n for n in newsletters}
    added: list[str] = []
    for sender, last_seen_iso in senders_seen.items():
        last_seen_date = last_seen_iso[:10] if last_seen_iso else today
        if sender in by_sender:
            by_sender[sender]["lastSeen"] = last_seen_date
        else:
            entry = {
                "name": sender,
                "sender": sender,
                "topic": "Uncategorized",
                "status": "active",
                "techRelevant": True,
                "lastSeen": last_seen_date,
                "unsubscribe": False,
                "notes": f"Added by digest pipeline {today}.",
            }
            newsletters.append(entry)
            by_sender[sender] = entry
            added.append(sender)
    return newsletters, added


def process_unsubscribes(newsletters: list[dict], inbox_id: str, api_key: str,
                          *, verbose: bool = False) -> list[str]:
    """For every registry entry flagged unsubscribe=true and not already
    unsubscribed, find that sender's most recent message, look for a
    one-click URL in the raw body, and fetch it. Returns human-readable
    notes for anything left for the reader to handle by hand (an opaque
    redirect link, or no link found at all)."""
    flagged: list[str] = []
    for entry in newsletters:
        if not entry.get("unsubscribe") or entry.get("status") == "unsubscribed":
            continue
        sender = entry["sender"]
        try:
            recent = client.list_messages(inbox_id, api_key, limit=5, from_sender=sender)
        except RuntimeError as e:
            flagged.append(f"{sender}: could not list messages ({e})")
            continue
        if not recent:
            flagged.append(f"{sender}: no recent message to search for an unsubscribe link")
            continue
        thread_id = recent[0]["thread_id"]
        try:
            thread = client.get_thread(inbox_id, thread_id, api_key)
        except RuntimeError as e:
            flagged.append(f"{sender}: could not read thread ({e})")
            continue
        msgs = thread.get("messages") or []
        text = "\n".join(m.get("text", "") for m in msgs)
        html = "\n".join(m.get("html", "") for m in msgs)
        url = unsub.find_unsubscribe_url(text, html)
        if not url:
            flagged.append(f"{sender}: no unsubscribe link found in latest message")
            continue
        if not unsub.is_one_click(url):
            flagged.append(f"{sender}: only an opaque redirect link found, not a one-click "
                            f"URL, left for manual click: {url}")
            continue
        if unsub.unsubscribe(url):
            entry["status"] = "unsubscribed"
            entry["unsubscribe"] = False
            utils.log(f"newsletter: unsubscribed from {sender}", verbose=verbose)
        else:
            flagged.append(f"{sender}: one-click unsubscribe request failed: {url}")
    return flagged


def delete_used_threads(inbox_id: str, thread_ids: list[str], api_key: str,
                         *, verbose: bool = False) -> list[str]:
    """Delete every thread whose content was used in a successfully
    published digest. Only call this after a successful publish."""
    errors: list[str] = []
    for thread_id in thread_ids:
        try:
            client.delete_thread(inbox_id, thread_id, api_key)
            utils.log(f"newsletter: deleted thread {thread_id}", verbose=verbose)
        except RuntimeError as e:
            errors.append(f"delete_thread {thread_id} crashed: {e}")
    return errors
