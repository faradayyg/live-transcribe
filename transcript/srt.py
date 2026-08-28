"""SRT subtitle file generation from transcript segments."""

from transcript.models import TranscriptSegment


def format_srt_timestamp(seconds: float) -> str:
    """Convert a float number of seconds to SRT timestamp format: HH:MM:SS,mmm"""
    if seconds < 0:
        seconds = 0.0
    total_ms = int(round(seconds * 1000))
    hours = total_ms // 3_600_000
    total_ms %= 3_600_000
    minutes = total_ms // 60_000
    total_ms %= 60_000
    secs = total_ms // 1000
    millis = total_ms % 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def generate_srt(segments: list[TranscriptSegment]) -> str:
    """Generate SRT content from a list of finalized transcript segments."""
    lines: list[str] = []
    index = 1
    for segment in segments:
        text = segment.text.strip()
        if not text:
            continue
        start = format_srt_timestamp(segment.start)
        end = format_srt_timestamp(segment.end)
        lines.append(str(index))
        lines.append(f"{start} --> {end}")
        lines.append(text)
        lines.append("")
        index += 1
    return "\n".join(lines)
