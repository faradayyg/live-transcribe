"""Tests for Bible reference parsing and detection."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from bible.parser import resolve_book
from bible.detector import detect
from transcript.models import BibleReference


# ---------------------------------------------------------------------------
# resolve_book
# ---------------------------------------------------------------------------

class TestResolveBook:
    def test_canonical(self):
        assert resolve_book("John") == "John"

    def test_canonical_lowercase(self):
        assert resolve_book("john") == "John"

    def test_abbreviation(self):
        assert resolve_book("Rom") == "Romans"
        assert resolve_book("1Co") == "1 Corinthians"
        assert resolve_book("Ps") == "Psalms"
        assert resolve_book("Psa") == "Psalms"

    def test_spoken_ordinal(self):
        assert resolve_book("first corinthians") == "1 Corinthians"
        assert resolve_book("second timothy") == "2 Timothy"
        assert resolve_book("third john") == "3 John"

    def test_spoken_numbered(self):
        assert resolve_book("1 corinthians") == "1 Corinthians"
        assert resolve_book("2 samuel") == "2 Samuel"

    def test_unknown(self):
        assert resolve_book("Zzz") is None
        assert resolve_book("") is None


# ---------------------------------------------------------------------------
# detect — written forms
# ---------------------------------------------------------------------------

class TestDetectWritten:
    def test_john_3_16(self):
        ref = detect("Let's read John 3:16 together")
        assert ref is not None
        assert ref.book == "John"
        assert ref.chapter == 3
        assert ref.verse_start == 16
        assert ref.verse_end is None

    def test_romans_8_1(self):
        ref = detect("Romans 8:1")
        assert ref is not None
        assert ref.book == "Romans"
        assert ref.chapter == 8
        assert ref.verse_start == 1

    def test_verse_range(self):
        ref = detect("Turn to Romans 8:1-4")
        assert ref is not None
        assert ref.verse_start == 1
        assert ref.verse_end == 4

    def test_matthew_beatitudes(self):
        ref = detect("Matthew 5:3-12")
        assert ref is not None
        assert ref.book == "Matthew"
        assert ref.chapter == 5
        assert ref.verse_start == 3
        assert ref.verse_end == 12

    def test_psalm_23(self):
        ref = detect("Psalm 23")
        assert ref is not None
        assert ref.book == "Psalms"
        assert ref.chapter == 23

    def test_1_corinthians_13(self):
        ref = detect("1 Corinthians 13")
        assert ref is not None
        assert ref.book == "1 Corinthians"
        assert ref.chapter == 13

    def test_philippians_4_13(self):
        ref = detect("Philippians 4:13 says I can do all things")
        assert ref is not None
        assert ref.book == "Philippians"
        assert ref.chapter == 4
        assert ref.verse_start == 13


# ---------------------------------------------------------------------------
# detect — spoken forms
# ---------------------------------------------------------------------------

class TestDetectSpoken:
    def test_john_chapter_3_verse_16(self):
        ref = detect("Turn to John chapter 3 verse 16")
        assert ref is not None
        assert ref.book == "John"
        assert ref.chapter == 3
        assert ref.verse_start == 16

    def test_romans_chapter_8(self):
        ref = detect("Romans chapter 8")
        assert ref is not None
        assert ref.book == "Romans"
        assert ref.chapter == 8

    def test_first_corinthians_chapter_13(self):
        ref = detect("First Corinthians chapter 13")
        assert ref is not None
        assert ref.book == "1 Corinthians"
        assert ref.chapter == 13

    def test_spoken_verse_range_through(self):
        ref = detect("Romans chapter 8 verses 1 through 4")
        assert ref is not None
        assert ref.book == "Romans"
        assert ref.chapter == 8
        assert ref.verse_start == 1
        assert ref.verse_end == 4


# ---------------------------------------------------------------------------
# detect — invalid / empty
# ---------------------------------------------------------------------------

class TestDetectInvalid:
    def test_empty_string(self):
        assert detect("") is None

    def test_no_reference(self):
        assert detect("Welcome everyone to today's service") is None

    def test_partial_gibberish(self):
        # Should not crash
        result = detect("Zzz 99:99")
        assert result is None

    def test_none_safe(self):
        # detect() must never raise
        try:
            detect(None)  # type: ignore
        except Exception:
            pytest.fail("detect(None) raised an exception")

    def test_very_long_text(self):
        # Should complete without error
        text = "We are reading today from " + ("the Bible " * 500) + "John 3:16"
        ref = detect(text)
        assert ref is not None
        assert ref.book == "John"
