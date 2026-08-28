"""Transcript context buffer and Bible-reference session history."""

from __future__ import annotations

from transcript.models import BibleReference, TranscriptSegment


class TranscriptContextBuffer:
    """Rolling window of finalized transcript text for Bible detection."""

    def __init__(self, window_seconds: float = 5.0) -> None:
        self._window = window_seconds
        self._segments: list[TranscriptSegment] = []

    def add_segment(self, seg: TranscriptSegment) -> str:
        """Add *seg*, trim old segments, return combined context text."""
        self._segments.append(seg)
        if len(self._segments) > 1:
            cutoff = self._segments[-1].end - self._window
            self._segments = [s for s in self._segments if s.end >= cutoff]
        return self.get_text()

    def get_text(self) -> str:
        return " ".join(s.text for s in self._segments)

    def clear(self) -> None:
        self._segments.clear()


class ReferenceHistory:
    """Session Bible reference history with deduplication and upgrade support.

    History is stored newest-first. Deduplication uses normalized_key().
    A chapter-only entry is upgraded when a verse-specific entry for the same
    book/chapter arrives.
    """

    def __init__(self) -> None:
        self._history: list[BibleReference] = []

    def add_or_upgrade(self, ref: BibleReference) -> str:
        """
        Returns "added", "upgraded", or "duplicate".
        "upgraded" = chapter-only entry replaced with verse-specific one
        """
        new_key = ref.normalized_key()
        for i, existing in enumerate(self._history):
            if existing.normalized_key() == new_key:
                return "duplicate"
            if (
                existing.book == ref.book
                and existing.chapter == ref.chapter
                and existing.verse_start is None
                and ref.verse_start is not None
            ):
                self._history[i] = ref
                return "upgraded"
        self._history.insert(0, ref)
        return "added"

    def get_all(self) -> list[BibleReference]:
        return list(self._history)

    def clear(self) -> None:
        self._history.clear()
