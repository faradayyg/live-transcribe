"""
Tests for the transcription engine abstraction and provider factory.

All tests avoid real network connections.  OpenAI and Deepgram event handling
is tested by calling internal methods directly.
"""

from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import json
import time
from unittest.mock import MagicMock, patch

import pytest

from transcript.models import TranscriptSegment
from transcription.engine import TranscriptionEngine
from transcription.deepgram_engine import DeepgramEngine
from transcription.openai_engine import OpenAIEngine
from transcription import create_engine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_openai_delta_event(item_id: str, delta: str) -> dict:
    return {
        "type": "conversation.item.input_audio_transcription.delta",
        "item_id": item_id,
        "delta": delta,
    }


def _make_openai_completed_event(item_id: str, transcript: str) -> dict:
    return {
        "type": "conversation.item.input_audio_transcription.completed",
        "item_id": item_id,
        "transcript": transcript,
    }


def _make_openai_session_created_event() -> dict:
    return {"type": "transcription_session.created", "session": {}}


def _make_openai_speech_started_event() -> dict:
    return {"type": "input_audio_buffer.speech_started"}


def _make_openai_error_event(code: str, message: str) -> dict:
    return {"type": "error", "error": {"code": code, "message": message}}


# ---------------------------------------------------------------------------
# 1. Interface conformance — both engines subclass TranscriptionEngine
# ---------------------------------------------------------------------------

class TestInterfaceConformance:

    def test_deepgram_is_transcription_engine(self):
        assert issubclass(DeepgramEngine, TranscriptionEngine)

    def test_openai_is_transcription_engine(self):
        assert issubclass(OpenAIEngine, TranscriptionEngine)

    def test_deepgram_has_required_methods(self):
        engine = DeepgramEngine()
        for method in ("connect", "send_audio", "pause", "resume", "disconnect"):
            assert callable(getattr(engine, method, None)), f"Missing: {method}"

    def test_openai_has_required_methods(self):
        engine = OpenAIEngine()
        for method in ("connect", "send_audio", "pause", "resume", "disconnect"):
            assert callable(getattr(engine, method, None)), f"Missing: {method}"

    def test_deepgram_has_callback_setters(self):
        engine = DeepgramEngine()
        for method in ("set_transcript_callback", "set_error_callback", "set_status_callback"):
            assert callable(getattr(engine, method, None)), f"Missing: {method}"

    def test_openai_has_callback_setters(self):
        engine = OpenAIEngine()
        for method in ("set_transcript_callback", "set_error_callback", "set_status_callback"):
            assert callable(getattr(engine, method, None)), f"Missing: {method}"


# ---------------------------------------------------------------------------
# 2. Provider factory
# ---------------------------------------------------------------------------

class TestProviderFactory:

    def test_create_deepgram_engine(self):
        engine = create_engine("Deepgram")
        assert isinstance(engine, DeepgramEngine)

    def test_create_openai_engine(self):
        engine = create_engine("OpenAI")
        assert isinstance(engine, OpenAIEngine)

    def test_factory_case_insensitive(self):
        assert isinstance(create_engine("deepgram"), DeepgramEngine)
        assert isinstance(create_engine("openai"),   OpenAIEngine)
        assert isinstance(create_engine("  OpenAI  "), OpenAIEngine)

    def test_factory_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown transcription provider"):
            create_engine("UnknownProvider")

    def test_factory_returns_independent_instances(self):
        e1 = create_engine("OpenAI")
        e2 = create_engine("OpenAI")
        assert e1 is not e2


# ---------------------------------------------------------------------------
# 3. OpenAI — session events
# ---------------------------------------------------------------------------

class TestOpenAISessionEvents:

    def _engine_with_callbacks(self):
        engine = OpenAIEngine()
        statuses = []
        engine.set_status_callback(lambda s: statuses.append(s))
        return engine, statuses

    def test_session_created_emits_live_status(self):
        engine, statuses = self._engine_with_callbacks()
        engine._handle_event(_make_openai_session_created_event())
        assert statuses == ["Live"]

    def test_speech_started_resets_interim_text(self):
        engine = OpenAIEngine()
        engine._interim_text = "previous text"
        engine._session_start = time.monotonic()
        engine._handle_event(_make_openai_speech_started_event())
        assert engine._interim_text == ""

    def test_speech_started_sets_speech_start(self):
        engine = OpenAIEngine()
        engine._session_start = time.monotonic() - 2.0
        before = time.monotonic()
        engine._handle_event(_make_openai_speech_started_event())
        after = time.monotonic()
        # _speech_start should be set to a monotonic value within the window
        assert before <= engine._speech_start <= after


# ---------------------------------------------------------------------------
# 4. OpenAI — interim events
# ---------------------------------------------------------------------------

class TestOpenAIInterimEvents:

    def _engine_with_transcript_callback(self):
        engine = OpenAIEngine()
        engine._session_start = time.monotonic()
        engine._speech_start  = engine._session_start
        received: list[TranscriptSegment] = []
        engine.set_transcript_callback(lambda seg: received.append(seg))
        return engine, received

    def test_delta_emits_interim_segment(self):
        engine, received = self._engine_with_transcript_callback()
        engine._handle_event(_make_openai_delta_event("id1", "Hello "))
        assert len(received) == 1
        assert received[0].final is False
        assert "Hello" in received[0].text

    def test_delta_accumulates_text(self):
        engine, received = self._engine_with_transcript_callback()
        engine._handle_event(_make_openai_delta_event("id1", "Hello "))
        engine._handle_event(_make_openai_delta_event("id1", "world"))
        assert received[-1].text == "Hello world"

    def test_interim_segment_has_float_timestamps(self):
        engine, received = self._engine_with_transcript_callback()
        engine._handle_event(_make_openai_delta_event("id1", "Test"))
        seg = received[0]
        assert isinstance(seg.start, float)
        assert isinstance(seg.end, float)

    def test_empty_delta_not_emitted(self):
        engine, received = self._engine_with_transcript_callback()
        engine._handle_event(_make_openai_delta_event("id1", ""))
        assert received == []


# ---------------------------------------------------------------------------
# 5. OpenAI — final events
# ---------------------------------------------------------------------------

class TestOpenAIFinalEvents:

    def _engine_with_transcript_callback(self):
        engine = OpenAIEngine()
        engine._session_start = time.monotonic()
        engine._speech_start  = engine._session_start
        received: list[TranscriptSegment] = []
        engine.set_transcript_callback(lambda seg: received.append(seg))
        return engine, received

    def test_completed_emits_final_segment(self):
        engine, received = self._engine_with_transcript_callback()
        engine._handle_event(_make_openai_completed_event("id1", "Let's look at Romans 8."))
        assert len(received) == 1
        seg = received[0]
        assert seg.final is True
        assert seg.text == "Let's look at Romans 8."

    def test_completed_clears_interim_text(self):
        engine, received = self._engine_with_transcript_callback()
        engine._interim_text = "partial text"
        engine._handle_event(_make_openai_completed_event("id1", "Full sentence."))
        assert engine._interim_text == ""

    def test_empty_completed_not_emitted(self):
        engine, received = self._engine_with_transcript_callback()
        engine._handle_event(_make_openai_completed_event("id1", ""))
        assert received == []

    def test_whitespace_only_completed_not_emitted(self):
        engine, received = self._engine_with_transcript_callback()
        engine._handle_event(_make_openai_completed_event("id1", "   "))
        assert received == []

    def test_final_segment_timestamps_are_float(self):
        engine, received = self._engine_with_transcript_callback()
        engine._handle_event(_make_openai_completed_event("id1", "Faith is key."))
        seg = received[0]
        assert isinstance(seg.start, float)
        assert isinstance(seg.end, float)

    def test_end_is_after_start(self):
        engine, received = self._engine_with_transcript_callback()
        # Make speech_start behind session_start (simulated delay)
        engine._session_start = time.monotonic() - 5.0
        engine._speech_start  = time.monotonic() - 2.0
        engine._handle_event(_make_openai_completed_event("id1", "A sentence."))
        seg = received[0]
        assert seg.end >= seg.start


# ---------------------------------------------------------------------------
# 6. OpenAI — error events
# ---------------------------------------------------------------------------

class TestOpenAIErrorEvents:

    def test_error_event_emits_error_callback(self):
        engine = OpenAIEngine()
        errors: list[str] = []
        engine.set_error_callback(lambda e: errors.append(e))
        engine._handle_event(_make_openai_error_event("service_unavailable", "Overloaded"))
        assert len(errors) == 1
        assert "Overloaded" in errors[0]

    def test_auth_error_produces_clear_message(self):
        engine = OpenAIEngine()
        errors: list[str] = []
        engine.set_error_callback(lambda e: errors.append(e))
        engine._handle_event(_make_openai_error_event("invalid_api_key", "Invalid key"))
        assert len(errors) == 1
        assert "authentication" in errors[0].lower() or "OPENAI_API_KEY" in errors[0]

    def test_missing_api_key_emits_clear_error(self):
        """connect() with no key set should emit a clear error, not crash."""
        engine = OpenAIEngine()
        errors: list[str] = []
        engine.set_error_callback(lambda e: errors.append(e))
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("OPENAI_API_KEY", None)
            engine.connect()
        # Give the connect path time to fire the callback synchronously
        # (connect() detects missing key before spawning a thread)
        assert len(errors) == 1
        assert "OPENAI_API_KEY" in errors[0]

    def test_missing_api_key_does_not_start_thread(self):
        engine = OpenAIEngine()
        engine.set_error_callback(lambda _: None)
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("OPENAI_API_KEY", None)
            engine.connect()
        assert engine._ws_thread is None


# ---------------------------------------------------------------------------
# 7. SRT works with OpenAI-produced segments
# ---------------------------------------------------------------------------

class TestSRTWithOpenAISegments:

    def test_srt_from_openai_segments(self):
        from transcript.srt import generate_srt

        segments = [
            TranscriptSegment(start=1.0, end=4.5, text="Welcome everyone.", final=True),
            TranscriptSegment(start=4.5, end=8.0, text="Let us look at Romans 8.", final=True),
        ]
        srt = generate_srt(segments)
        assert "Welcome everyone." in srt
        assert "Romans 8" in srt
        assert "-->" in srt

    def test_srt_only_uses_final_segments(self):
        from transcript.srt import generate_srt

        segments = [
            TranscriptSegment(start=0.0, end=2.0, text="Final sentence.", final=True),
        ]
        srt = generate_srt(segments)
        assert "Final sentence." in srt


# ---------------------------------------------------------------------------
# 8. Bible detection with OpenAI-produced segments
# ---------------------------------------------------------------------------

class TestBibleDetectionWithOpenAIOutput:

    def test_bible_detected_from_openai_segment(self):
        from bible.detector import detect

        seg = TranscriptSegment(
            start=0.0, end=3.0,
            text="Let's turn to John chapter 3 verse 16.",
            final=True,
        )
        ref = detect(seg.text)
        assert ref is not None
        assert ref.book == "John"
        assert ref.chapter == 3
        assert ref.verse_start == 16

    def test_no_bible_ref_returns_none(self):
        from bible.detector import detect

        seg = TranscriptSegment(
            start=0.0, end=2.0,
            text="Good morning everyone.",
            final=True,
        )
        ref = detect(seg.text)
        assert ref is None


# ---------------------------------------------------------------------------
# 9. TranscriptSegment is provider-independent
# ---------------------------------------------------------------------------

class TestTranscriptSegmentModel:

    def test_segment_fields(self):
        seg = TranscriptSegment(start=1.0, end=3.5, text="Hello", final=True)
        assert seg.start == 1.0
        assert seg.end == 3.5
        assert seg.text == "Hello"
        assert seg.final is True

    def test_interim_segment(self):
        seg = TranscriptSegment(start=0.0, end=0.5, text="partial", final=False)
        assert seg.final is False
