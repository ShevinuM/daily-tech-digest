"""Google AI Studio (Gemini) client — plain urllib, no SDK, matching this
project's zero-dependency philosophy. Called twice per run (selection, then
summarization), not once per item, to stay well within the free tier.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

API_BASE = "https://generativelanguage.googleapis.com/v1beta"
TIMEOUT = 60
MAX_ATTEMPTS = 5
BACKOFF_BASE = 5  # seconds; sleeps are 5, 10, 20, 40 between the 5 attempts (~75s total)
RETRYABLE_HTTP_CODES = {429, 500, 502, 503, 504}


def generate_json(prompt: str, *, api_key: str, model: str = "gemini-2.5-flash",
                   temperature: float = 0.2, timeout: int = TIMEOUT):
    """Send one prompt, require a JSON response, return it parsed (list or dict)."""
    url = f"{API_BASE}/models/{model}:generateContent"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "responseMimeType": "application/json",
        },
    }
    req_body = json.dumps(payload).encode("utf-8")

    data = None
    for attempt in range(MAX_ATTEMPTS):
        req = urllib.request.Request(
            url, method="POST", data=req_body,
            headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read())
            break
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            if e.code not in RETRYABLE_HTTP_CODES or attempt == MAX_ATTEMPTS - 1:
                raise RuntimeError(f"Gemini generateContent -> HTTP {e.code}: {detail}") from e
        except (urllib.error.URLError, OSError) as e:
            reason = getattr(e, "reason", e)
            if attempt == MAX_ATTEMPTS - 1:
                raise RuntimeError(f"Gemini generateContent -> {reason}") from e
        time.sleep(BACKOFF_BASE * (2 ** attempt))

    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Gemini response missing expected content: {data}") from e

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Gemini did not return valid JSON: {text[:500]}") from e
