"""Configuration for the Bible reference detection/resolution system.

All values can be overridden via environment variables before the application
starts.  Defaults are chosen to be sane out-of-the-box.
"""
from __future__ import annotations

import os

# Whether to use the LLM resolver at all.  Set to "false" / "0" / "no" to
# disable and fall back to the local regex detector only.
BIBLE_LLM_ENABLED: bool = (
    os.environ.get("BIBLE_LLM_ENABLED", "true").lower()
    not in ("0", "false", "no")
)

# OpenAI model used for Bible-reference resolution.  Should be a model that
# supports JSON mode (json_object response_format).
BIBLE_LLM_MODEL: str = os.environ.get("BIBLE_LLM_MODEL", "gpt-4o-mini")

# Minimum confidence from the LLM response required to accept a reference.
BIBLE_REFERENCE_CONFIDENCE_THRESHOLD: float = float(
    os.environ.get("BIBLE_REFERENCE_CONFIDENCE_THRESHOLD", "0.85")
)

# Rolling context window fed to the LLM (seconds of finalised transcript).
BIBLE_CONTEXT_SECONDS: float = float(
    os.environ.get("BIBLE_CONTEXT_SECONDS", "20.0")
)

# Debounce delay (ms) before submitting a candidate to the LLM.
# New transcript segments that arrive during this window reset the timer,
# so rapid consecutive segments produce a single combined LLM call.
BIBLE_DEBOUNCE_MS: int = int(os.environ.get("BIBLE_DEBOUNCE_MS", "800"))
