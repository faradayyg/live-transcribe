import time
import uuid
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TranscriptSegment:
    start: float
    end: float
    text: str
    final: bool
    speaker: Optional[int] = None  # reserved for future speaker diarization


@dataclass
class BibleReference:
    book: str
    chapter: int
    verse_start: Optional[int]
    verse_end: Optional[int]
    confidence: float
    raw_text: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    detected_at: float = field(default_factory=time.monotonic)
    source_text: str = ""

    def display(self) -> str:
        ref = f"{self.book} {self.chapter}"
        if self.verse_start is not None:
            ref += f":{self.verse_start}"
            if self.verse_end is not None:
                ref += f"-{self.verse_end}"
        return ref

    def normalized_key(self) -> str:
        return f"{self.book}:{self.chapter}:{self.verse_start}:{self.verse_end}"
