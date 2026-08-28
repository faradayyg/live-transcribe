"""Tests for the LLM Bible-reference resolver and candidate gate."""
from __future__ import annotations

import json
import os
import sys
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from bible.resolver import _parse_response, is_candidate, resolve
from transcript.models import BibleReference


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_openai_response(references: list[dict]) -> MagicMock:
    """Build a mock openai ChatCompletion response."""
    msg = MagicMock()
    msg.content = json.dumps({"references": references})
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _mock_client(response: MagicMock) -> MagicMock:
    client = MagicMock()
    client.chat.completions.create.return_value = response
    return client


# ---------------------------------------------------------------------------
# is_candidate
# ---------------------------------------------------------------------------


class TestIsCandidate:
    def test_book_name_single_word(self):
        assert is_candidate("Let's read Romans today") is True

    def test_book_name_john(self):
        assert is_candidate("Go to John chapter three") is True

    def test_chapter_keyword_only(self):
        assert is_candidate("Turn to chapter eight") is True

    def test_verse_keyword(self):
        assert is_candidate("Read verse sixteen with me") is True

    def test_verses_keyword(self):
        assert is_candidate("Those verses are important") is True

    def test_through_keyword(self):
        assert is_candidate("one through four") is True

    def test_no_reference_general(self):
        assert is_candidate("Let us pray together in faith") is False

    def test_empty_string(self):
        assert is_candidate("") is False

    def test_psalms_triggers(self):
        assert is_candidate("Open your Bible to Psalms") is True

    def test_multi_word_book_name(self):
        assert is_candidate("first corinthians thirteen") is True

    def test_numbered_book(self):
        assert is_candidate("1 Corinthians") is True


# ---------------------------------------------------------------------------
# _parse_response — unit tests without network
# ---------------------------------------------------------------------------


class TestParseResponse:
    def test_basic_single_verse(self):
        data = {
            "references": [
                {
                    "book": "John",
                    "chapter": 3,
                    "verse_start": 16,
                    "verse_end": 16,
                    "confidence": 0.98,
                    "source_text": "John 3:16",
                }
            ]
        }
        refs = _parse_response(data)
        assert len(refs) == 1
        assert refs[0].book == "John"
        assert refs[0].chapter == 3
        assert refs[0].verse_start == 16
        # verse_end normalised: 16 == 16 → None
        assert refs[0].verse_end is None
        assert refs[0].confidence == pytest.approx(0.98)

    def test_verse_range(self):
        data = {
            "references": [
                {
                    "book": "Romans",
                    "chapter": 8,
                    "verse_start": 1,
                    "verse_end": 4,
                    "confidence": 0.96,
                    "source_text": "Romans 8:1-4",
                }
            ]
        }
        refs = _parse_response(data)
        assert len(refs) == 1
        assert refs[0].book == "Romans"
        assert refs[0].verse_start == 1
        assert refs[0].verse_end == 4

    def test_chapter_only(self):
        data = {
            "references": [
                {
                    "book": "Romans",
                    "chapter": 8,
                    "verse_start": None,
                    "verse_end": None,
                    "confidence": 0.85,
                    "source_text": "Romans chapter 8",
                }
            ]
        }
        refs = _parse_response(data)
        assert len(refs) == 1
        assert refs[0].verse_start is None
        assert refs[0].verse_end is None

    def test_empty_references(self):
        assert _parse_response({"references": []}) == []

    def test_unknown_book_skipped(self):
        data = {
            "references": [
                {
                    "book": "NotABook",
                    "chapter": 1,
                    "verse_start": 1,
                    "verse_end": None,
                    "confidence": 0.9,
                    "source_text": "NotABook 1:1",
                }
            ]
        }
        assert _parse_response(data) == []

    def test_multiple_references(self):
        data = {
            "references": [
                {
                    "book": "John",
                    "chapter": 3,
                    "verse_start": 16,
                    "verse_end": 16,
                    "confidence": 0.98,
                    "source_text": "John 3:16",
                },
                {
                    "book": "Romans",
                    "chapter": 8,
                    "verse_start": 1,
                    "verse_end": 4,
                    "confidence": 0.95,
                    "source_text": "Romans 8:1-4",
                },
            ]
        }
        refs = _parse_response(data)
        assert len(refs) == 2
        assert refs[0].book == "John"
        assert refs[1].book == "Romans"

    def test_alias_book_name_normalised(self):
        # LLM might return "1Co" or "First Corinthians"
        data = {
            "references": [
                {
                    "book": "First Corinthians",
                    "chapter": 13,
                    "verse_start": 4,
                    "verse_end": 7,
                    "confidence": 0.93,
                    "source_text": "First Corinthians 13:4-7",
                }
            ]
        }
        refs = _parse_response(data)
        assert len(refs) == 1
        assert refs[0].book == "1 Corinthians"

    def test_psalms_canonical(self):
        data = {
            "references": [
                {
                    "book": "Psalm",
                    "chapter": 23,
                    "verse_start": None,
                    "verse_end": None,
                    "confidence": 0.9,
                    "source_text": "Psalm 23",
                }
            ]
        }
        refs = _parse_response(data)
        assert len(refs) == 1
        assert refs[0].book == "Psalms"

    def test_malformed_item_skipped_others_kept(self):
        data = {
            "references": [
                {"book": "John", "chapter": "bad", "verse_start": 16, "verse_end": 16, "confidence": 0.9, "source_text": ""},
                {"book": "Romans", "chapter": 8, "verse_start": 1, "verse_end": None, "confidence": 0.9, "source_text": ""},
            ]
        }
        refs = _parse_response(data)
        assert len(refs) == 1
        assert refs[0].book == "Romans"


# ---------------------------------------------------------------------------
# resolve() — with mocked OpenAI client
# ---------------------------------------------------------------------------


class TestResolve:
    def test_basic_reference(self):
        mock_resp = _mock_openai_response([
            {"book": "John", "chapter": 3, "verse_start": 16, "verse_end": 16,
             "confidence": 0.98, "source_text": "John 3:16"}
        ])
        with patch("bible.resolver._get_openai_client", return_value=_mock_client(mock_resp)):
            refs = resolve("Let's read John 3:16 together.")
        assert len(refs) == 1
        assert refs[0].book == "John"
        assert refs[0].chapter == 3
        assert refs[0].verse_start == 16

    def test_spoken_reference(self):
        mock_resp = _mock_openai_response([
            {"book": "John", "chapter": 3, "verse_start": 16, "verse_end": 16,
             "confidence": 0.97, "source_text": "John chapter three verse sixteen"}
        ])
        with patch("bible.resolver._get_openai_client", return_value=_mock_client(mock_resp)):
            refs = resolve("John chapter three verse sixteen")
        assert len(refs) == 1
        assert refs[0].chapter == 3
        assert refs[0].verse_start == 16

    def test_spoken_range(self):
        mock_resp = _mock_openai_response([
            {"book": "Romans", "chapter": 8, "verse_start": 1, "verse_end": 4,
             "confidence": 0.96, "source_text": "Romans chapter eight verses one through four"}
        ])
        with patch("bible.resolver._get_openai_client", return_value=_mock_client(mock_resp)):
            refs = resolve("Romans chapter eight verses one through four")
        assert len(refs) == 1
        assert refs[0].book == "Romans"
        assert refs[0].verse_start == 1
        assert refs[0].verse_end == 4

    def test_rapid_fire_verses(self):
        mock_resp = _mock_openai_response([
            {"book": "John", "chapter": 3, "verse_start": 16, "verse_end": 19,
             "confidence": 0.95, "source_text": "John 3:16, 17, 18, and 19"}
        ])
        with patch("bible.resolver._get_openai_client", return_value=_mock_client(mock_resp)):
            refs = resolve("John 3:16, 17, 18, and 19")
        assert len(refs) == 1
        assert refs[0].verse_start == 16
        assert refs[0].verse_end == 19

    def test_no_reference_returns_empty(self):
        mock_resp = _mock_openai_response([])
        with patch("bible.resolver._get_openai_client", return_value=_mock_client(mock_resp)):
            refs = resolve("Let's talk about faith and grace.")
        assert refs == []

    def test_multiple_references(self):
        mock_resp = _mock_openai_response([
            {"book": "John", "chapter": 3, "verse_start": 16, "verse_end": 16,
             "confidence": 0.98, "source_text": "John 3:16"},
            {"book": "Romans", "chapter": 8, "verse_start": 1, "verse_end": 4,
             "confidence": 0.95, "source_text": "Romans 8:1-4"},
        ])
        with patch("bible.resolver._get_openai_client", return_value=_mock_client(mock_resp)):
            refs = resolve("John 3:16 and Romans 8:1-4")
        assert len(refs) == 2

    def test_continuation_with_current_ref(self):
        # "verse 2" after Romans 8:1 → LLM should return Romans 8:2
        mock_resp = _mock_openai_response([
            {"book": "Romans", "chapter": 8, "verse_start": 2, "verse_end": 2,
             "confidence": 0.90, "source_text": "verse 2"}
        ])
        current = BibleReference("Romans", 8, 1, None, 0.95, "Romans 8:1")
        with patch("bible.resolver._get_openai_client", return_value=_mock_client(mock_resp)):
            refs = resolve("and verse 2", current_ref=current)
        assert len(refs) == 1
        assert refs[0].book == "Romans"
        assert refs[0].chapter == 8
        assert refs[0].verse_start == 2

    def test_ambiguous_returns_empty(self):
        mock_resp = _mock_openai_response([])
        with patch("bible.resolver._get_openai_client", return_value=_mock_client(mock_resp)):
            refs = resolve("Let's go back to that chapter.")
        assert refs == []

    def test_invalid_json_returns_empty(self):
        msg = MagicMock()
        msg.content = "not json at all"
        choice = MagicMock()
        choice.message = msg
        resp = MagicMock()
        resp.choices = [choice]
        client = _mock_client(resp)
        with patch("bible.resolver._get_openai_client", return_value=client):
            refs = resolve("Romans 8:1")
        assert refs == []

    def test_cross_segment_context(self):
        # Combined context from multiple segments
        combined = "Let's read Romans chapter eight verses one through four together."
        mock_resp = _mock_openai_response([
            {"book": "Romans", "chapter": 8, "verse_start": 1, "verse_end": 4,
             "confidence": 0.97, "source_text": combined}
        ])
        with patch("bible.resolver._get_openai_client", return_value=_mock_client(mock_resp)):
            refs = resolve(combined)
        assert len(refs) == 1
        assert refs[0].display() == "Romans 8:1-4"

    def test_separate_references_not_merged(self):
        mock_resp = _mock_openai_response([
            {"book": "Romans", "chapter": 8, "verse_start": 1, "verse_end": 1,
             "confidence": 0.97, "source_text": "Romans 8:1"},
            {"book": "John", "chapter": 3, "verse_start": 16, "verse_end": 16,
             "confidence": 0.98, "source_text": "John 3:16"},
        ])
        with patch("bible.resolver._get_openai_client", return_value=_mock_client(mock_resp)):
            refs = resolve("Romans 8:1. Now let's look at John 3:16.")
        assert len(refs) == 2
        books = {r.book for r in refs}
        assert "Romans" in books
        assert "John" in books


# ---------------------------------------------------------------------------
# Integration: resolver output → ReferenceHistory deduplication
# ---------------------------------------------------------------------------


class TestResolverWithHistory:
    def test_deduplication_from_overlapping_context(self):
        from bible.context import ReferenceHistory

        history = ReferenceHistory()
        # Simulate two LLM results for the same reference (overlapping context)
        ref_a = BibleReference("Romans", 8, 1, 4, 0.96, "Romans 8:1-4")
        ref_b = BibleReference("Romans", 8, 1, 4, 0.95, "Romans chapter eight verses one to four")

        r1 = history.add_or_upgrade(ref_a)
        r2 = history.add_or_upgrade(ref_b)

        assert r1 == "added"
        assert r2 == "duplicate"
        assert len(history.get_all()) == 1

    def test_chapter_upgraded_to_verse_specific(self):
        from bible.context import ReferenceHistory

        history = ReferenceHistory()
        chapter_ref = BibleReference("Romans", 8, None, None, 0.85, "Romans chapter 8")
        verse_ref = BibleReference("Romans", 8, 1, 4, 0.96, "Romans 8:1-4")

        history.add_or_upgrade(chapter_ref)
        result = history.add_or_upgrade(verse_ref)

        assert result == "upgraded"
        assert history.get_all()[0].verse_start == 1
        assert history.get_all()[0].verse_end == 4

    def test_selection_independent_of_history(self):
        from bible.context import ReferenceHistory

        history = ReferenceHistory()
        ref1 = BibleReference("Romans", 8, 1, None, 0.96, "Romans 8:1")
        ref2 = BibleReference("John", 3, 16, None, 0.98, "John 3:16")
        ref3 = BibleReference("Psalms", 23, None, None, 0.85, "Psalm 23")

        history.add_or_upgrade(ref1)
        history.add_or_upgrade(ref2)
        history.add_or_upgrade(ref3)

        # History newest-first: Psalm 23, John 3:16, Romans 8:1
        all_refs = history.get_all()
        assert len(all_refs) == 3
        assert all_refs[0].book == "Psalms"

        # Selecting an older reference does not change history
        selected = all_refs[2]  # Romans 8:1
        assert selected.book == "Romans"
        assert len(history.get_all()) == 3  # unchanged
