"""Tests for pure helpers in ui.main_window."""

from ui.main_window import _level_to_meter


class TestLevelToMeter:
    def test_silence_is_empty(self):
        assert _level_to_meter(0.0) == 0
        assert _level_to_meter(0.0001) == 0

    def test_full_scale_is_full(self):
        assert _level_to_meter(1.0) == 100

    def test_typical_speech_sits_mid_meter(self):
        assert 40 <= _level_to_meter(0.03) <= 60
