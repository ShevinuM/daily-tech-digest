"""Extractive summarization via sumy — turns each pool-3 item's article text
into a short, factual summary before the one LLM call rewrites it into
prose. TextRank beats LexRank here: on the same real article, LexRank
produced 853 chars of disjointed fragments, TextRank 1329 chars that read as
connected prose (see PLAN.md).
"""
from __future__ import annotations

import threading

import nltk
from sumy.nlp.stemmers import Stemmer
from sumy.nlp.tokenizers import Tokenizer
from sumy.parsers.plaintext import PlaintextParser
from sumy.summarizers.lex_rank import LexRankSummarizer
from sumy.summarizers.text_rank import TextRankSummarizer
from sumy.utils import get_stop_words

from rank import enrich

LANGUAGE = "english"
MAX_INPUT_CHARS = 20_000
MIN_ARTICLE_TEXT_CHARS = 400

_SUMMARIZERS = {"text_rank": TextRankSummarizer, "lex_rank": LexRankSummarizer}

_nltk_ready = False
_nltk_lock = threading.Lock()


def _ensure_nltk_data() -> None:
    """CI pre-downloads punkt_tab (see .github/workflows/digest.yml); this
    only fires on a fresh local checkout. `punkt_tab`, not the pre-3.8.2
    `punkt`, is the resource nltk 3.9+ actually looks up."""
    global _nltk_ready
    if _nltk_ready:
        return
    with _nltk_lock:
        if _nltk_ready:
            return
        probe = "Warm-up sentence. Second one."
        try:
            Tokenizer(LANGUAGE).to_sentences(probe)
        except LookupError:
            nltk.download("punkt_tab", quiet=True)
            try:
                Tokenizer(LANGUAGE).to_sentences(probe)
            except LookupError as e:
                raise RuntimeError(
                    "nltk punkt_tab data unavailable even after a download attempt; "
                    "run python3 -c \"import nltk; nltk.download('punkt_tab')\" manually") from e
        _nltk_ready = True


def pick_text(item: dict) -> str:
    """Which field to summarize from: `article_text` unless it's missing or
    too thin to summarize meaningfully (under ~400 chars), falling back to
    `description`, then `title`. `article_text` already went through
    rank/enrich.clean_for_summary when it was set; the description/title
    fallback hasn't, so it's routed through the same cleaner here — a raw
    RSS description or a URL-as-title (see rank/prompt.py's `_scrub` for the
    other half of this invariant) must never become sumy's only input."""
    article_text = item.get("article_text") or ""
    if len(article_text) >= MIN_ARTICLE_TEXT_CHARS:
        return article_text
    fallback = item.get("description") or article_text or item.get("title") or ""
    return enrich.clean_for_summary(fallback)


def _truncate_at_sentence(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    for sep in (". ", "! ", "? "):
        idx = cut.rfind(sep)
        if idx > 0:
            return cut[:idx + 1].strip()
    return cut.strip()


def extractive(text: str, *, sentences: int = 5, max_chars: int = 900,
               algorithm: str = "text_rank") -> str:
    text = (text or "").strip()
    if not text:
        return ""
    text = text[:MAX_INPUT_CHARS]

    _ensure_nltk_data()

    parser = PlaintextParser.from_string(text, Tokenizer(LANGUAGE))
    summarizer_cls = _SUMMARIZERS.get(algorithm, TextRankSummarizer)
    summarizer = summarizer_cls(Stemmer(LANGUAGE))
    summarizer.stop_words = get_stop_words(LANGUAGE)

    picked = summarizer(parser.document, sentences)
    summary = " ".join(str(s) for s in picked) if picked else text
    return _truncate_at_sentence(summary, max_chars)
