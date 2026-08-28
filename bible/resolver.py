"""LLM-based Bible reference resolver.

Architecture
------------
  is_candidate(text)
      Quick local check — uses book names + reference keywords.
      Deliberately permissive: decides whether to send text to the LLM,
      not whether the text *contains* a reference.

  resolve(context_text, current_ref) -> list[BibleReference]
      Synchronous LLM call.  Returns an empty list when the LLM finds no
      reference, or raises on hard errors (caller handles exceptions).

  BibleResolverWorker(QObject)
      Async Qt wrapper.  Lives in the main thread.
      - schedule(context, current_ref)  → debounce → thread-pool → LLM
      - refs_resolved Signal emitted back on the main thread when done.

The local regex detector (bible.detector.detect_all) remains available as
a fallback when BIBLE_LLM_ENABLED is False.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QObject, QTimer, Signal

from bible.config import BIBLE_DEBOUNCE_MS, BIBLE_LLM_ENABLED, BIBLE_LLM_MODEL
from bible.parser import BOOKS, resolve_book
from transcript.models import BibleReference

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Candidate-detection sets (built once)
# ---------------------------------------------------------------------------


def _build_book_name_set() -> set[str]:
    names: set[str] = set()
    for book in BOOKS:
        names.add(book["canonical"].lower())
        for alias in book["aliases"]:
            names.add(alias.lower())
        for spoken in book["spoken"]:
            # Only add single-word spoken forms to the fast word-match set;
            # multi-word forms (e.g. "song of solomon") are handled by the
            # regex fallback below.
            if " " not in spoken:
                names.add(spoken.lower())
    return names


_BOOK_NAME_WORDS: frozenset[str] = frozenset(_build_book_name_set())

# All known names (including multi-word) sorted longest-first for the regex
_ALL_BOOK_NAMES: list[str] = sorted(
    {
        n
        for book in BOOKS
        for n in [book["canonical"]] + book["aliases"] + book["spoken"]
    },
    key=len,
    reverse=True,
)

_BOOK_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(n) for n in _ALL_BOOK_NAMES) + r")\b",
    re.IGNORECASE,
)

_KEYWORD_RE = re.compile(
    r"\b(?:chapter|verse|verses|through|starting\s+at|continuing\s+in)\b",
    re.IGNORECASE,
)


def is_candidate(text: str) -> bool:
    """Return True if *text* may contain a Bible reference.

    Deliberately permissive — this is a gate, not a parser.
    """
    if not text:
        return False
    # Fast path: check individual words against the book-name set
    words = text.lower().split()
    for word in words:
        # Strip trailing punctuation
        word = word.rstrip(".,;:!?")
        if word in _BOOK_NAME_WORDS:
            return True
    # Slower path: multi-word book names + continuation keywords
    if _BOOK_RE.search(text) or _KEYWORD_RE.search(text):
        return True
    return False


# ---------------------------------------------------------------------------
# LLM client (lazy singleton, thread-safe)
# ---------------------------------------------------------------------------

_client_lock = threading.Lock()
_client: Optional[object] = None  # openai.OpenAI


def _get_openai_client():
    """Return a cached OpenAI client, creating it on first call."""
    global _client
    with _client_lock:
        if _client is None:
            try:
                import openai  # noqa: PLC0415
            except ImportError as exc:
                raise RuntimeError(
                    "openai package is not installed. Run: pip install openai>=1.0.0"
                ) from exc
            api_key = os.environ.get("OPENAI_API_KEY", "").strip()
            if not api_key:
                raise RuntimeError(
                    "OPENAI_API_KEY is not set. "
                    "Export it before starting the application."
                )
            _client = openai.OpenAI(api_key=api_key)
        return _client


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a Bible reference extractor for a live sermon transcription application.

TASK: Identify explicit Bible references in the provided transcript text.

OUTPUT FORMAT:
Respond with ONLY a JSON object — no prose, no markdown, no explanation.
The response must always match this exact structure:

{
  "references": [
    {
      "book": "Romans",
      "chapter": 8,
      "verse_start": 1,
      "verse_end": 4,
      "confidence": 0.96,
      "source_text": "Romans chapter eight verses one through four"
    }
  ]
}

When no reference is found, respond with exactly:

{
  "references": []
}

FIELD RULES:
- book        : canonical Bible book name (string)
- chapter     : chapter number (integer)
- verse_start : starting verse (integer), or null for chapter-only references
- verse_end   : ending verse (integer) for ranges, same as verse_start for a \
single verse, or null for chapter-only references
- confidence  : certainty 0.0–1.0 (number); use lower values for ambiguous cases
- source_text : the exact phrase from the transcript that triggered this \
identification (string)

DETECTION RULES:
1. Only extract references that are clearly and explicitly stated in the speech.
2. SINGLE VERSE ("John 3:16"): verse_start=16, verse_end=16.
3. RANGE ("Romans 8:1-4", "verses 1 through 4"): verse_start=1, verse_end=4.
4. CHAPTER ONLY ("Romans chapter 8"): verse_start=null, verse_end=null.
5. CONTINUATION: When the text says "verse 3" or "through 5" without naming a \
new book or chapter, AND an active reference is provided, apply the continuation \
to that active reference's book and chapter — only when unambiguous.
6. CANONICAL NAMES: Use standard book names.
   "First/1st Corinthians" → "1 Corinthians"
   "Psalm" or "Psalms" → "Psalms"
   Strip "the book of" prefixes.
7. NO HALLUCINATION: If the text is ambiguous or contains no clear reference, \
return {"references": []}. Do not guess.
8. MULTIPLE REFERENCES: Return a separate entry for each distinct reference.
   "Romans 8:1. Now John 3:16." → two entries.
9. Do NOT generate Bible text — only identify references.
"""


def _build_user_message(
    context_text: str,
    current_ref: Optional[BibleReference],
) -> str:
    parts = [f"Transcript context:\n{context_text}"]
    if current_ref:
        parts.append(f"Currently active reference: {current_ref.display()}")
    parts.append(
        'Extract all Bible references mentioned. Return JSON: {"references": [...]}'
    )
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def _parse_response(data: dict) -> list[BibleReference]:
    """Convert raw LLM JSON into BibleReference objects."""
    results: list[BibleReference] = []
    for item in data.get("references", []):
        try:
            book_raw = str(item.get("book", "")).strip()
            canonical = resolve_book(book_raw)
            if canonical is None:
                log.debug("LLM returned unknown book: %r", book_raw)
                continue

            chapter = item.get("chapter")
            if not isinstance(chapter, int) or chapter < 1:
                continue

            verse_start = item.get("verse_start")
            verse_end = item.get("verse_end")
            confidence = float(item.get("confidence", 0.0))
            source_text = str(item.get("source_text", ""))

            # Normalise: if verse_end == verse_start, treat as single verse
            if (
                verse_start is not None
                and verse_end is not None
                and verse_end == verse_start
            ):
                verse_end = None

            ref = BibleReference(
                book=canonical,
                chapter=chapter,
                verse_start=verse_start,
                verse_end=verse_end,
                confidence=confidence,
                raw_text=source_text,
                source_text=source_text,
            )
            results.append(ref)
        except Exception as exc:
            log.debug("Skipping malformed LLM reference item: %s", exc)
    return results


# ---------------------------------------------------------------------------
# Public resolver function
# ---------------------------------------------------------------------------


def resolve(
    context_text: str,
    current_ref: Optional[BibleReference] = None,
    *,
    model: str = BIBLE_LLM_MODEL,
) -> list[BibleReference]:
    """Synchronous LLM call.

    Returns a (possibly empty) list of BibleReference objects.
    Raises RuntimeError/openai.OpenAIError on failure — callers should
    catch and log rather than crash.
    """
    client = _get_openai_client()
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": _build_user_message(context_text, current_ref)},
    ]
    response = client.chat.completions.create(  # type: ignore[union-attr]
        model=model,
        response_format={"type": "json_object"},
        messages=messages,
        max_tokens=512,
        temperature=0,
    )

    raw = response.choices[0].message.content or "{}"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("LLM returned non-JSON response: %r", raw[:200])
        return []
    return _parse_response(data)


# ---------------------------------------------------------------------------
# Qt async worker
# ---------------------------------------------------------------------------


class BibleResolverWorker(QObject):
    """Async, debounced LLM Bible-reference resolver for the Qt main thread.

    Usage::

        worker = BibleResolverWorker()
        worker.refs_resolved.connect(my_slot)   # list[BibleReference]

        # Call from main thread whenever a candidate segment arrives:
        worker.schedule(context_text, current_ref)

        # On application shutdown:
        worker.shutdown()

    The worker debounces rapid segment arrivals so that "Romans chapter eight"
    followed 200 ms later by "verse one through four" produces a single LLM
    call, not two.
    """

    refs_resolved = Signal(list)  # list[BibleReference], delivered on main thread

    def __init__(self, debounce_ms: int = BIBLE_DEBOUNCE_MS) -> None:
        super().__init__()
        self._executor = ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="bible_llm"
        )
        self._pending_context: str = ""
        self._pending_ref: Optional[BibleReference] = None
        self._last_submitted: str = ""

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(debounce_ms)
        self._timer.timeout.connect(self._submit)

    def schedule(
        self,
        context_text: str,
        current_ref: Optional[BibleReference] = None,
    ) -> None:
        """Debounce and schedule an LLM resolve. Call from the main thread."""
        if not BIBLE_LLM_ENABLED:
            return
        self._pending_context = context_text
        self._pending_ref = current_ref
        # Re-start timer so rapid consecutive segments are batched
        self._timer.start()

    def _submit(self) -> None:
        """Timer callback — fires on the main thread after debounce expires."""
        context = self._pending_context
        current_ref = self._pending_ref
        if not context:
            return
        # Skip if context is identical to the last submission
        if context == self._last_submitted:
            return
        self._last_submitted = context

        future = self._executor.submit(resolve, context, current_ref)
        future.add_done_callback(self._on_done)

    def _on_done(self, future) -> None:
        """Callback from the thread pool — may run on any thread.

        Qt auto-detects the cross-thread signal emission and queues the
        delivery to the main thread.
        """
        try:
            refs = future.result()
            if refs:
                # Safe to emit from a non-Qt thread; Qt queues it
                self.refs_resolved.emit(refs)
        except Exception as exc:
            log.warning("Bible LLM resolver error: %s", exc)

    def shutdown(self) -> None:
        """Stop the debounce timer and drain the thread pool."""
        self._timer.stop()
        self._executor.shutdown(wait=False, cancel_futures=True)
