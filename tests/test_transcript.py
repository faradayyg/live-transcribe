"""Tests for TranscriptManager and plain-text generation."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from transcript.manager import TranscriptManager
from transcript.models import TranscriptSegment


def _final(text: str, start: float = 0.0, end: float = 1.0) -> TranscriptSegment:
    return TranscriptSegment(start=start, end=end, text=text, final=True)


def _interim(text: str) -> TranscriptSegment:
    return TranscriptSegment(start=0.0, end=0.5, text=text, final=False)


class TestTranscriptManager:

    def test_add_final(self):
        m = TranscriptManager()
        m.add_final(_final("Hello world."))
        assert len(m.get_segments()) == 1
        assert m.get_segments()[0].text == "Hello world."

    def test_blank_segments_skipped(self):
        m = TranscriptManager()
        m.add_final(_final(""))
        m.add_final(_final("   "))
        assert len(m.get_segments()) == 0

    def test_multiple_finals(self):
        m = TranscriptManager()
        m.add_final(_final("First."))
        m.add_final(_final("Second."))
        m.add_final(_final("Third."))
        assert len(m.get_segments()) == 3

    def test_interim_updated(self):
        m = TranscriptManager()
        m.update_interim(_interim("And when we look at"))
        assert m.interim is not None
        assert m.interim.text == "And when we look at"

        # Update replaces, not appends
        m.update_interim(_interim("And when we look at the"))
        assert m.interim.text == "And when we look at the"

    def test_clear_interim(self):
        m = TranscriptManager()
        m.update_interim(_interim("something"))
        m.clear_interim()
        assert m.interim is None

    def test_get_final_text(self):
        m = TranscriptManager()
        m.add_final(_final("Welcome everyone."))
        m.add_final(_final("Today we talk about faith."))
        text = m.get_final_text()
        assert "Welcome everyone." in text
        assert "Today we talk about faith." in text
        # Segments are separated
        assert "\n\n" in text

    def test_get_final_text_empty(self):
        m = TranscriptManager()
        assert m.get_final_text() == ""

    def test_clear(self):
        m = TranscriptManager()
        m.add_final(_final("Line one."))
        m.update_interim(_interim("partial"))
        m.clear()
        assert len(m.get_segments()) == 0
        assert m.interim is None

    def test_segments_order_preserved(self):
        m = TranscriptManager()
        texts = ["Alpha.", "Beta.", "Gamma.", "Delta."]
        for t in texts:
            m.add_final(_final(t))
        for i, seg in enumerate(m.get_segments()):
            assert seg.text == texts[i]

    def test_get_segments_returns_copy(self):
        """Mutating the returned list must not affect the manager."""
        m = TranscriptManager()
        m.add_final(_final("A."))
        segs = m.get_segments()
        segs.clear()
        assert len(m.get_segments()) == 1
