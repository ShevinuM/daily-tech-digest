"""Multi-provider LLM client — plain urllib, no SDK, matching this project's
zero-dependency philosophy.

Tries providers in order (Gemini, then any fallback with an API key set),
falling through on failure. This exists because free-tier hosted models
routinely go down for reasons outside our control: Gemini's free tier
returns 503 "high demand" for stretches of minutes to hours, and fallback
providers' free tiers cap tokens/minute low enough that our larger
(selection) prompt alone can exceed the cap — that shows up as a 429 and
should fail over fast, not burn a long backoff.

Called once per run — see rank/prompt.py.
"""
from __future__ import annotations

import functools
import json
import os
import time
import urllib.error
import urllib.request

TIMEOUT = 60
SERVER_ERROR_ATTEMPTS = 5
SERVER_ERROR_BACKOFF_BASE = 5  # seconds; sleeps are 5, 10, 20, 40 (~75s total)
RATE_LIMIT_ATTEMPTS = 2
RATE_LIMIT_BACKOFF_BASE = 2  # seconds
RETRYABLE_SERVER_CODES = {500, 502, 503, 504}

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"


class ProviderError(RuntimeError):
    """A single provider's call failed; the caller should try the next one."""


def _request_with_retry(url: str, headers: dict, body: bytes, *, timeout: int = TIMEOUT) -> dict:
    server_attempts = 0
    rate_limit_attempts = 0
    while True:
        req = urllib.request.Request(url, method="POST", data=body, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            if e.code == 429:
                rate_limit_attempts += 1
                if rate_limit_attempts >= RATE_LIMIT_ATTEMPTS:
                    raise ProviderError(f"HTTP 429: {detail}") from e
                time.sleep(RATE_LIMIT_BACKOFF_BASE * rate_limit_attempts)
                continue
            if e.code in RETRYABLE_SERVER_CODES:
                server_attempts += 1
                if server_attempts >= SERVER_ERROR_ATTEMPTS:
                    raise ProviderError(f"HTTP {e.code}: {detail}") from e
                time.sleep(SERVER_ERROR_BACKOFF_BASE * (2 ** (server_attempts - 1)))
                continue
            raise ProviderError(f"HTTP {e.code}: {detail}") from e
        except (urllib.error.URLError, OSError) as e:
            server_attempts += 1
            reason = getattr(e, "reason", e)
            if server_attempts >= SERVER_ERROR_ATTEMPTS:
                raise ProviderError(str(reason)) from e
            time.sleep(SERVER_ERROR_BACKOFF_BASE * (2 ** (server_attempts - 1)))


def _call_gemini(prompt: str, *, api_key: str, model: str, temperature: float) -> str:
    url = f"{GEMINI_API_BASE}/models/{model}:generateContent"
    headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "responseMimeType": "application/json",
        },
    }
    data = _request_with_retry(url, headers, json.dumps(payload).encode("utf-8"))
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        raise ProviderError(f"missing expected content: {data}") from e


def _call_openai_compat(prompt: str, *, api_key: str, model: str, temperature: float,
                         base_url: str) -> str:
    url = f"{base_url}/chat/completions"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "response_format": {"type": "json_object"},
    }
    data = _request_with_retry(url, headers, json.dumps(payload).encode("utf-8"))
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        raise ProviderError(f"missing expected content: {data}") from e


# Order matters: tried top to bottom, first one with an API key set that
# succeeds wins. The one remaining call's payload is now ~6-7k tokens (all
# mechanical filtering/scoring/summarizing happens in Python before this —
# see rank/pools.py, rank/relevance.py, rank/summarize.py), which fits
# comfortably under Groq/OpenRouter's free-tier token-per-minute caps too,
# so they're genuinely usable as a Gemini-outage fallback now rather than a
# guaranteed 429.
PROVIDERS = [
    {"name": "gemini", "env": "GEMINI_API_KEY", "default_model": "gemini-3.6-flash",
     "call": _call_gemini},
    {"name": "groq", "env": "GROQ_API_KEY", "default_model": "llama-3.3-70b-versatile",
     "call": functools.partial(_call_openai_compat, base_url="https://api.groq.com/openai/v1")},
    {"name": "openrouter", "env": "OPENROUTER_API_KEY", "default_model": "openai/gpt-oss-20b:free",
     "call": functools.partial(_call_openai_compat, base_url="https://openrouter.ai/api/v1")},
]
PROVIDER_ENV_VARS = [p["env"] for p in PROVIDERS]


def generate_json(prompt: str, *, config: dict, temperature: float = 0.2):
    """Send one prompt to the first configured provider that succeeds,
    require a JSON response, return it parsed (list or dict)."""
    llm_cfg = config.get("llm", {})
    errors = []
    tried = 0
    for provider in PROVIDERS:
        api_key = os.environ.get(provider["env"])
        if not api_key:
            continue
        tried += 1
        model = llm_cfg.get(provider["name"], {}).get("model", provider["default_model"])
        try:
            text = provider["call"](prompt, api_key=api_key, model=model, temperature=temperature)
        except ProviderError as e:
            errors.append(f"{provider['name']} ({model}): {e}")
            continue
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            errors.append(f"{provider['name']} ({model}): did not return valid JSON: {text[:300]}")
            continue

    if tried == 0:
        raise RuntimeError(
            "no LLM provider configured (set one of: " + ", ".join(PROVIDER_ENV_VARS) + ")")
    raise RuntimeError("all LLM providers failed:\n" + "\n".join(errors))
