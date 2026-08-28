from transcript.models import TranscriptSegment
from typing import Optional


class TranscriptManager:
    """Holds finalized transcript segments and the current interim segment."""

    def __init__(self):
        self._segments: list[TranscriptSegment] = []
        self._interim: Optional[TranscriptSegment] = None

    def add_final(self, segment: TranscriptSegment) -> None:
        if segment.text.strip():
            self._segments.append(segment)

    def update_interim(self, segment: TranscriptSegment) -> None:
        self._interim = segment

    def clear_interim(self) -> None:
        self._interim = None

    @property
    def interim(self) -> Optional[TranscriptSegment]:
        return self._interim

    def get_segments(self) -> list[TranscriptSegment]:
        return list(self._segments)

    def get_final_text(self) -> str:
        return "\n\n".join(s.text.strip() for s in self._segments if s.text.strip())

    def clear(self) -> None:
        self._segments.clear()
        self._interim = None
