"""Google AI Studio (Gemini) client — plain urllib, no SDK, matching this
project's zero-dependency philosophy. Called twice per run (selection, then
summarization), not once per item, to stay well within the free tier.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

API_BASE = "https://generativelanguage.googleapis.com/v1beta"
TIMEOUT = 60


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
    req = urllib.request.Request(
        url, method="POST",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        raise RuntimeError(f"Gemini generateContent -> HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Gemini generateContent -> {e.reason}") from e

    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Gemini response missing expected content: {data}") from e

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Gemini did not return valid JSON: {text[:500]}") from e
