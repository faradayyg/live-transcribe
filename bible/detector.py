"""
Bible reference detector.

Scans a piece of transcript text and returns the first (highest-confidence)
BibleReference found.  All lookup is local — no network calls.

Supported written forms
-----------------------
  John 3:16
  Romans 8:1
  Romans 8:1-4
  Psalm 23
  1 Corinthians 13
  Matthew 5:3-12

Supported spoken/dictated forms
--------------------------------
  John chapter 3 verse 16
  Romans chapter 8
  Psalm 23
  First Corinthians chapter 13
  Romans chapter 8 verses 1 through 4
  Romans chapter 8 verse 1 to verse 4
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from bible.parser import BOOKS, ORDINAL_WORDS, resolve_book
from transcript.models import BibleReference

# ---------------------------------------------------------------------------
# Number-word helpers
# ---------------------------------------------------------------------------

_NUM_WORDS: dict[str, int] = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    "hundred": 100,
}


def _word_to_int(text: str) -> Optional[int]:
    """Convert a number word / digit string to int, or return None."""
    text = text.strip().lower()
    if text.isdigit():
        return int(text)
    if text in _NUM_WORDS:
        return _NUM_WORDS[text]
    # e.g. "twenty three" → 23
    parts = text.split()
    if len(parts) == 2 and parts[0] in _NUM_WORDS and parts[1] in _NUM_WORDS:
        return _NUM_WORDS[parts[0]] + _NUM_WORDS[parts[1]]
    return None


# ---------------------------------------------------------------------------
# Build a regex alternation of all book names (longest first to avoid
# matching "John" before "1 John" etc.)
# ---------------------------------------------------------------------------

def _build_book_pattern() -> str:
    names: list[str] = []
    for book in BOOKS:
        names.append(book["canonical"])
        names += book["aliases"]
        names += book["spoken"]
    # Replace ordinal word prefixes with (digit|word) alternatives
    expanded: list[str] = []
    for name in names:
        escaped = re.escape(name)
        expanded.append(escaped)
    # Sort longest first so the regex engine matches greedily
    expanded.sort(key=len, reverse=True)
    return "(?:" + "|".join(expanded) + ")"


_BOOK_PAT = _build_book_pattern()

# Number fragment: digit(s) or a single number-word
_NUM = r"(?:\d+|" + "|".join(re.escape(w) for w in sorted(_NUM_WORDS, key=len, reverse=True)) + r")"

# Optional verse range suffix: "-4" / "to verse 4" / "through 4"
_VERSE_END = r"(?:\s*[-–]\s*(?P<ve>" + _NUM + r")|(?:\s+(?:to\s+(?:verse\s+)?|through\s+)(?P<ve2>" + _NUM + r")))"

# ---------------------------------------------------------------------------
# Compiled patterns (ordered: most specific first)
# ---------------------------------------------------------------------------

# Written: "Romans 8:1-4", "John 3:16"
_PAT_WRITTEN = re.compile(
    r"(?P<book>" + _BOOK_PAT + r")"
    r"\s+(?P<ch>\d+)"
    r":(?P<vs>\d+)"
    r"(?:\s*[-–]\s*(?P<ve>\d+))?",
    re.IGNORECASE,
)

# Spoken full: "Romans chapter 8 verse 1 to verse 4"
_PAT_SPOKEN_FULL = re.compile(
    r"(?P<book>" + _BOOK_PAT + r")"
    r"\s+chapter\s+(?P<ch>" + _NUM + r")"
    r"\s+verses?\s+(?P<vs>" + _NUM + r")"
    r"(?:\s+(?:through|to)\s+(?:verse\s+)?(?P<ve>" + _NUM + r"))?",
    re.IGNORECASE,
)

# Rapid-fire written: "John 3:16, 17, 18, and 19"
_PAT_RAPID_FIRE = re.compile(
    r"(?P<book>" + _BOOK_PAT + r")"
    r"\s+(?P<ch>\d+):(?P<vs>\d+)"
    r"(?P<more>(?:\s*,\s*(?:and\s+)?\d+)+)",
    re.IGNORECASE,
)

# Spoken multi-verse: "Romans chapter 8, verses 1, 2, 3, and 4"
_PAT_SPOKEN_MULTI = re.compile(
    r"(?P<book>" + _BOOK_PAT + r")"
    r"\s+chapter\s+(?P<ch>" + _NUM + r")"
    r",?\s+(?:and\s+)?verses?\s+(?P<vs>" + _NUM + r")"
    r"(?P<more>(?:\s*,\s*(?:and\s+)?(?:" + _NUM + r"))*)",
    re.IGNORECASE,
)

# Spoken chapter only: "Romans chapter 8"
_PAT_SPOKEN_CH = re.compile(
    r"(?P<book>" + _BOOK_PAT + r")"
    r"\s+chapter\s+(?P<ch>" + _NUM + r")",
    re.IGNORECASE,
)

# Written chapter only (no colon): "Psalm 23", "1 Corinthians 13"
_PAT_CHAPTER_ONLY = re.compile(
    r"(?P<book>" + _BOOK_PAT + r")"
    r"\s+(?P<ch>\d+)"
    r"(?!\s*:\d)",  # negative lookahead: don't match if followed by :digit
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Bible verse data — loaded from bible/KJV/bible.json
#
# The KJV file uses:
#   "Psalm"          instead of "Psalms"
#   "Song Of Solomon" instead of "Song of Solomon"
# ---------------------------------------------------------------------------

_KJV_BOOK_MAP: dict[str, str] = {
    "Psalms":           "Psalm",
    "Song of Solomon":  "Song Of Solomon",
}

_VERSE_DB: Optional[dict] = None


def _load_verse_db() -> dict:
    global _VERSE_DB
    if _VERSE_DB is not None:
        return _VERSE_DB
    # Prefer the complete KJV file; fall back to the bundled stub.
    kjv_path  = Path(__file__).parent / "KJV" / "bible.json"
    stub_path = Path(__file__).parent / "bible.json"
    path = kjv_path if kjv_path.exists() else stub_path
    try:
        with path.open(encoding="utf-8") as f:
            _VERSE_DB = json.load(f)
    except Exception:
        _VERSE_DB = {}
    return _VERSE_DB


def lookup_verse(book: str, chapter: int, verse: int) -> Optional[str]:
    """
    Return KJV verse text for *book* chapter:verse, or None.

    Handles both the complete KJV format  {book: {chapter: {verse: text}}}
    and the older stub format             {"verses": {"Book:ch:v": text}}.
    """
    db = _load_verse_db()
    if not db:
        return None

    # Complete KJV format
    kjv_book = _KJV_BOOK_MAP.get(book, book)
    book_data = db.get(kjv_book)
    if isinstance(book_data, dict):
        ch_data = book_data.get(str(chapter))
        if isinstance(ch_data, dict):
            return ch_data.get(str(verse))

    # Legacy stub format
    key = f"{book}:{chapter}:{verse}"
    return db.get("verses", {}).get(key)


# ---------------------------------------------------------------------------
# Core detector
# ---------------------------------------------------------------------------


def _make_ref(book_raw: str, ch_raw: str, vs_raw: Optional[str],
              ve_raw: Optional[str], raw_text: str,
              confidence: float) -> Optional[BibleReference]:
    canonical = resolve_book(book_raw)
    if canonical is None:
        return None
    ch = _word_to_int(ch_raw)
    if ch is None:
        return None
    vs = _word_to_int(vs_raw) if vs_raw else None
    ve = _word_to_int(ve_raw) if ve_raw else None
    return BibleReference(
        book=canonical,
        chapter=ch,
        verse_start=vs,
        verse_end=ve,
        confidence=confidence,
        raw_text=raw_text,
    )


def detect(text: str) -> Optional[BibleReference]:
    """
    Scan *text* and return the first BibleReference detected, or None.

    Detection never raises — failures return None silently.
    """
    if not text:
        return None
    try:
        return _detect(text)
    except Exception:
        return None


def _collect_all_matches(text: str) -> list[tuple[int, int, BibleReference]]:
    results: list[tuple[int, int, BibleReference]] = []

    for m in _PAT_RAPID_FIRE.finditer(text):
        digits = re.findall(r"\d+", m.group("more"))
        ve_str = digits[-1] if digits else None
        ref = _make_ref(
            m.group("book"), m.group("ch"), m.group("vs"), ve_str, m.group(0), 0.95
        )
        if ref:
            results.append((m.start(), m.end(), ref))

    num_word_pat = "|".join(
        re.escape(w) for w in sorted(_NUM_WORDS, key=len, reverse=True)
    )
    for m in _PAT_SPOKEN_MULTI.finditer(text):
        more_text = m.group("more") or ""
        num_tokens = re.findall(r"\d+|" + num_word_pat, more_text, re.IGNORECASE)
        ve_str = None
        if num_tokens:
            ve_val = _word_to_int(num_tokens[-1])
            ve_str = str(ve_val) if ve_val else None
        ref = _make_ref(
            m.group("book"), m.group("ch"), m.group("vs"), ve_str, m.group(0), 0.95
        )
        if ref:
            results.append((m.start(), m.end(), ref))

    for m in _PAT_SPOKEN_FULL.finditer(text):
        ref = _make_ref(
            m.group("book"), m.group("ch"), m.group("vs"), m.group("ve"), m.group(0), 0.95
        )
        if ref:
            results.append((m.start(), m.end(), ref))

    for m in _PAT_WRITTEN.finditer(text):
        ref = _make_ref(
            m.group("book"), m.group("ch"), m.group("vs"), m.group("ve"), m.group(0), 0.95
        )
        if ref:
            results.append((m.start(), m.end(), ref))

    for m in _PAT_SPOKEN_CH.finditer(text):
        ref = _make_ref(m.group("book"), m.group("ch"), None, None, m.group(0), 0.85)
        if ref:
            results.append((m.start(), m.end(), ref))

    for m in _PAT_CHAPTER_ONLY.finditer(text):
        ref = _make_ref(m.group("book"), m.group("ch"), None, None, m.group(0), 0.75)
        if ref:
            results.append((m.start(), m.end(), ref))

    return results


def _detect_all(text: str) -> list[BibleReference]:
    candidates = _collect_all_matches(text)
    if not candidates:
        return []
    candidates.sort(key=lambda x: (x[0], -(x[1] - x[0])))
    results: list[BibleReference] = []
    covered_end = -1
    for start, end, ref in candidates:
        if start >= covered_end:
            results.append(ref)
            covered_end = end
    return results


def detect_all(text: str) -> list[BibleReference]:
    """Return all non-overlapping BibleReferences in text. Never raises."""
    if not text:
        return []
    try:
        return _detect_all(text)
    except Exception:
        return []


def _detect(text: str) -> Optional[BibleReference]:
    # Try most-specific patterns first
    for m in _PAT_SPOKEN_FULL.finditer(text):
        ref = _make_ref(
            m.group("book"), m.group("ch"), m.group("vs"),
            m.group("ve"), m.group(0), 0.95,
        )
        if ref:
            return ref

    for m in _PAT_WRITTEN.finditer(text):
        ref = _make_ref(
            m.group("book"), m.group("ch"), m.group("vs"),
            m.group("ve"), m.group(0), 0.95,
        )
        if ref:
            return ref

    for m in _PAT_SPOKEN_CH.finditer(text):
        ref = _make_ref(
            m.group("book"), m.group("ch"), None, None, m.group(0), 0.85,
        )
        if ref:
            return ref

    for m in _PAT_CHAPTER_ONLY.finditer(text):
        ref = _make_ref(
            m.group("book"), m.group("ch"), None, None, m.group(0), 0.75,
        )
        if ref:
            return ref

    return None
