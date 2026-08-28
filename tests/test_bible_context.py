"""Tests for transcript context buffering and Bible reference history."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from bible.context import ReferenceHistory, TranscriptContextBuffer
from bible.detector import detect_all, lookup_verse
from transcript.models import BibleReference, TranscriptSegment
from ui.main_window import MainWindow


def _final_segment(text: str, start: float, end: float) -> TranscriptSegment:
    return TranscriptSegment(start=start, end=end, text=text, final=True)


class TestTranscriptContextBuffer:
    def test_add_segment_returns_combined_text(self):
        buffer = TranscriptContextBuffer(window_seconds=10)

        text = buffer.add_segment(_final_segment("Romans chapter 8", 0.0, 2.0))

        assert text == "Romans chapter 8"
        assert buffer.get_text() == "Romans chapter 8"

    def test_old_segments_are_trimmed_by_window(self):
        buffer = TranscriptContextBuffer(window_seconds=5)
        buffer.add_segment(_final_segment("first bit", 0.0, 2.0))
        buffer.add_segment(_final_segment("second bit", 3.0, 7.0))

        text = buffer.add_segment(_final_segment("third bit", 8.0, 11.0))

        assert text == "second bit third bit"

    def test_clear_empties_buffer(self):
        buffer = TranscriptContextBuffer()
        buffer.add_segment(_final_segment("Psalm 23", 0.0, 1.0))

        buffer.clear()

        assert buffer.get_text() == ""


class TestReferenceHistory:
    def test_add_upgrade_and_duplicate(self):
        history = ReferenceHistory()
        chapter_only = BibleReference("Romans", 8, None, None, 0.85, "Romans chapter 8")
        verse_range = BibleReference("Romans", 8, 1, 4, 0.95, "Romans chapter 8 verses 1 through 4")

        assert history.add_or_upgrade(chapter_only) == "added"
        assert history.add_or_upgrade(verse_range) == "upgraded"
        assert history.add_or_upgrade(
            BibleReference("Romans", 8, 1, 4, 0.95, "Romans 8:1-4")
        ) == "duplicate"

        saved = history.get_all()
        assert len(saved) == 1
        assert saved[0].display() == "Romans 8:1-4"

    def test_new_entries_are_newest_first(self):
        history = ReferenceHistory()
        first = BibleReference("John", 3, 16, None, 0.95, "John 3:16")
        second = BibleReference("Psalms", 23, None, None, 0.75, "Psalm 23")

        history.add_or_upgrade(first)
        history.add_or_upgrade(second)

        assert [ref.display() for ref in history.get_all()] == ["Psalms 23", "John 3:16"]


class TestDetectAll:
    def test_detects_multiple_non_overlapping_references(self):
        refs = detect_all("Read John 3:16 and Romans 8:1-4 today.")

        assert [ref.display() for ref in refs] == ["John 3:16", "Romans 8:1-4"]

    def test_detects_rapid_fire_written_reference(self):
        refs = detect_all("Please read John 3:16, 17, 18, and 19 together.")

        assert len(refs) == 1
        assert refs[0].book == "John"
        assert refs[0].chapter == 3
        assert refs[0].verse_start == 16
        assert refs[0].verse_end == 19

    def test_detects_spoken_multi_verse_reference(self):
        refs = detect_all("Romans chapter 8, verses 1, 2, 3, and 4 gives hope.")

        assert len(refs) == 1
        assert refs[0].display() == "Romans 8:1-4"

    def test_cross_segment_context_upgrades_reference(self):
        buffer = TranscriptContextBuffer(window_seconds=25)

        buffer.add_segment(_final_segment("Let's read Romans chapter 8", 0.0, 2.0))
        context_text = buffer.add_segment(_final_segment("verses 1 through 4 together.", 2.1, 4.0))
        refs = detect_all(context_text)

        assert len(refs) == 1
        assert refs[0].display() == "Romans 8:1-4"

    def test_history_deduplicates_repeated_detected_references(self):
        history = ReferenceHistory()
        refs = detect_all("John 3:16 and later John 3:16 again.")

        results = [history.add_or_upgrade(ref) for ref in refs]

        assert results == ["added", "duplicate"]
        assert [ref.display() for ref in history.get_all()] == ["John 3:16"]


class TestVerseRangeDisplayHelper:
    def test_get_verse_range_text_returns_joined_range(self):
        window = MainWindow.__new__(MainWindow)
        ref = BibleReference("John", 3, 16, 17, 0.95, "John 3:16-17")

        text = MainWindow._get_verse_range_text(window, ref)
        expected = "\n".join(
            f"{verse}  {lookup_verse('John', 3, verse)}"
            for verse in range(16, 18)
            if lookup_verse("John", 3, verse)
        )

        assert text == expected
