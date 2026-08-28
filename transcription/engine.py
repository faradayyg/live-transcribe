"""Abstract base class for transcription engines."""

from abc import ABC, abstractmethod
from typing import Callable, Optional

from transcript.models import TranscriptSegment


class TranscriptionEngine(ABC):

    def __init__(self) -> None:
        self._transcript_callback: Optional[Callable[[TranscriptSegment], None]] = None
        self._error_callback: Optional[Callable[[str], None]] = None
        self._status_callback: Optional[Callable[[str], None]] = None

    # ------------------------------------------------------------------
    # Callback registration
    # ------------------------------------------------------------------

    def set_transcript_callback(self, cb: Callable[[TranscriptSegment], None]) -> None:
        self._transcript_callback = cb

    def set_error_callback(self, cb: Callable[[str], None]) -> None:
        self._error_callback = cb

    def set_status_callback(self, cb: Callable[[str], None]) -> None:
        self._status_callback = cb

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    @abstractmethod
    def connect(self) -> None:
        """Establish the connection to the transcription service."""

    @abstractmethod
    def send_audio(self, audio_bytes: bytes) -> None:
        """Forward raw PCM bytes to the transcription service."""

    @abstractmethod
    def pause(self) -> None:
        """Pause audio streaming (keep connection alive)."""

    @abstractmethod
    def resume(self) -> None:
        """Resume audio streaming after a pause."""

    @abstractmethod
    def disconnect(self) -> None:
        """Close the connection cleanly."""
