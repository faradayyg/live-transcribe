"""Transcription engine package — factory and interface."""

from transcription.engine import TranscriptionEngine


def create_engine(provider: str) -> TranscriptionEngine:
    """Return a new engine instance for *provider* ('Deepgram' or 'OpenAI')."""
    name = provider.strip().lower()
    if name == "deepgram":
        from transcription.deepgram_engine import DeepgramEngine
        return DeepgramEngine()
    if name == "openai":
        from transcription.openai_engine import OpenAIEngine
        return OpenAIEngine()
    raise ValueError(f"Unknown transcription provider: {provider!r}")
