"""Tests for SRT timestamp formatting and generation."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from transcript.srt import format_srt_timestamp, generate_srt
from transcript.models import TranscriptSegment


# ---------------------------------------------------------------------------
# format_srt_timestamp
# ---------------------------------------------------------------------------

class TestFormatSrtTimestamp:
    def test_zero(self):
        assert format_srt_timestamp(0.0) == "00:00:00,000"

    def test_simple_seconds(self):
        assert format_srt_timestamp(5.2) == "00:00:05,200"

    def test_minutes(self):
        assert format_srt_timestamp(65.0) == "00:01:05,000"

    def test_hours(self):
        assert format_srt_timestamp(3661.5) == "01:01:01,500"

    def test_millis_rounding(self):
        assert format_srt_timestamp(8.700) == "00:00:08,700"

    def test_large_value(self):
        # 2h 30m 15.123s
        t = 2 * 3600 + 30 * 60 + 15.123
        assert format_srt_timestamp(t) == "02:30:15,123"

    def test_negative_clamped_to_zero(self):
        assert format_srt_timestamp(-1.0) == "00:00:00,000"


# ---------------------------------------------------------------------------
# generate_srt
# ---------------------------------------------------------------------------

def _seg(start, end, text, final=True) -> TranscriptSegment:
    return TranscriptSegment(start=start, end=end, text=text, final=final)


class TestGenerateSrt:
    def test_single_segment(self):
        srt = generate_srt([_seg(5.2, 8.7, "Welcome everyone.")])
        lines = srt.splitlines()
        assert lines[0] == "1"
        assert lines[1] == "00:00:05,200 --> 00:00:08,700"
        assert lines[2] == "Welcome everyone."

    def test_multiple_segments(self):
        segments = [
            _seg(5.2, 8.7, "Welcome everyone."),
            _seg(8.7, 13.4, "Today we talk about faith."),
        ]
        srt = generate_srt(segments)
        assert "1\n" in srt
        assert "2\n" in srt
        assert "Welcome everyone." in srt
        assert "Today we talk about faith." in srt

    def test_empty_segments_skipped(self):
        segments = [
            _seg(0, 1, ""),
            _seg(1, 2, "   "),
            _seg(2, 3, "Hello."),
        ]
        srt = generate_srt(segments)
        lines = [l for l in srt.splitlines() if l.strip()]
        assert lines[0] == "1"
        assert "Hello." in srt
        # No entry for blank segments
        assert "2\n" not in srt

    def test_empty_list(self):
        assert generate_srt([]) == ""

    def test_index_increments_correctly(self):
        segments = [_seg(i, i + 1, f"Line {i}") for i in range(5)]
        srt = generate_srt(segments)
        for i in range(1, 6):
            assert f"\n{i}\n" in f"\n{srt}" or srt.startswith(str(i))
