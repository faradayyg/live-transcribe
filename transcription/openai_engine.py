"""
OpenAI Realtime API streaming transcription engine.

Uses the OpenAI Realtime API (gpt-live-transcribe model) over WebSocket for
live, streaming speech-to-text.  The threading model is identical to
DeepgramEngine: a single background thread owns the WebSocket lifecycle and
two inner threads (send / recv) drive the data flow.

Send path  : caller → send_audio() → _audio_queue → _send_loop (resample → base64 → send)
Receive path: _recv_loop iterates WebSocket frames → _handle_event() → transcript callback

Audio format
------------
The engine accepts PCM16 mono 16 kHz (same as the Deepgram engine) and
resamples it to 24 kHz inside _send_loop before forwarding to OpenAI.  This
keeps the upstream audio pipeline unchanged.

Interim vs final
----------------
OpenAI emits ``conversation.item.input_audio_transcription.delta`` events
while the speaker is talking (interim) and a
``conversation.item.input_audio_transcription.completed`` event when the
turn ends (final).

API key
-------
Read from the ``OPENAI_API_KEY`` environment variable at connect time.

WebSocket endpoint
------------------
wss://api.openai.com/v1/realtime?model=gpt-live-transcribe

Required headers (current, non-beta shape)
    Authorization: Bearer <OPENAI_API_KEY>
    (No OpenAI-Beta header — the beta shape was disabled)
"""

from __future__ import annotations

import base64
import json
import logging
import os
import queue
import threading
import time
from typing import Optional

import numpy as np
import websockets.sync.client as ws_sync

from transcript.models import TranscriptSegment
from transcription.engine import TranscriptionEngine

log = logging.getLogger(__name__)

# OpenAI Realtime requires 24 kHz; our audio pipeline produces 16 kHz.
_INPUT_RATE = 16_000
_OUTPUT_RATE = 24_000


def _resample_16k_to_24k(pcm16_bytes: bytes) -> bytes:
    """Linear-interpolation resample from 16 kHz to 24 kHz (PCM16 mono)."""
    arr = np.frombuffer(pcm16_bytes, dtype=np.int16).astype(np.float32)
    if len(arr) == 0:
        return b""
    n_out = int(len(arr) * _OUTPUT_RATE / _INPUT_RATE)
    x_in = np.linspace(0.0, 1.0, len(arr))
    x_out = np.linspace(0.0, 1.0, n_out)
    resampled = np.interp(x_out, x_in, arr).astype(np.int16)
    return resampled.tobytes()


class OpenAIEngine(TranscriptionEngine):
    """Streams audio to OpenAI's Realtime transcription API via WebSocket."""

    MODEL = "gpt-live-transcribe"

    # Endpoint pattern (same as translation: /v1/realtime/translations?model=...):
    #   /v1/realtime/transcription_sessions?model=gpt-live-transcribe
    #
    # The model MUST appear as a query parameter — the endpoint uses it to
    # provision the right session backend.  Omitting it returns HTTP 403.
    # Using /v1/realtime?model=gpt-live-transcribe returns invalid_model
    # because gpt-live-transcribe is not supported at the /v1/realtime endpoint.
    WS_URL = "wss://api.openai.com/v1/realtime/transcription_sessions?model=gpt-live-transcribe"

    def __init__(self) -> None:
        super().__init__()
        self._api_key: str = ""
        self._audio_queue: queue.Queue[Optional[bytes]] = queue.Queue()
        self._ws_thread: Optional[threading.Thread] = None
        self._running = False
        self._paused = False
        # Wall-clock tracking for timestamps (API does not return per-word times)
        self._session_start: float = 0.0
        self._speech_start: float = 0.0
        self._interim_text: str = ""

    # ------------------------------------------------------------------
    # TranscriptionEngine interface
    # ------------------------------------------------------------------

    def connect(self) -> None:
        self._api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not self._api_key:
            self._emit_error(
                "OPENAI_API_KEY is not set.\n"
                "Export it before launching:\n"
                "  export OPENAI_API_KEY=your_key_here"
            )
            return

        self._running = True
        self._emit_status("Connecting")
        self._ws_thread = threading.Thread(
            target=self._ws_lifecycle, daemon=True, name="openai-ws"
        )
        self._ws_thread.start()

    def send_audio(self, audio_bytes: bytes) -> None:
        if not self._running:
            return
        self._audio_queue.put(audio_bytes)

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def disconnect(self) -> None:
        self._running = False
        self._audio_queue.put(None)  # sentinel — unblocks _send_loop
        if self._ws_thread and self._ws_thread.is_alive():
            self._ws_thread.join(timeout=5)
        self._ws_thread = None

    # ------------------------------------------------------------------
    # WebSocket lifecycle  (runs in _ws_thread)
    # ------------------------------------------------------------------

    def _ws_lifecycle(self) -> None:
        # No OpenAI-Beta header — beta API shape has been disabled
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            log.info("OpenAI: connecting to %s", self.WS_URL)
            with ws_sync.connect(
                self.WS_URL,
                additional_headers=headers,
                open_timeout=15,
            ) as ws:
                log.info("OpenAI: WebSocket connected")
                self._session_start = time.monotonic()
                self._interim_text = ""

                # Configure a transcription session with server VAD
                ws.send(
                    json.dumps(
                        {
                            "type": "session.update",
                            "session": {
                                "type": "transcription",
                                "audio": {
                                    "input": {
                                        "format": {
                                            "type": "audio/pcm",
                                            "rate": _OUTPUT_RATE,
                                        },
                                        "transcription": {
                                            "model": self.MODEL,
                                            "languages": [
                                                "en"
                                            ],  # plural array — required for gpt-live-transcribe
                                        },
                                        "turn_detection": {
                                            "type": "server_vad",
                                            "threshold": 0.5,
                                            "prefix_padding_ms": 300,
                                            "silence_duration_ms": 500,
                                        },
                                    }
                                },
                            },
                        }
                    )
                )

                # Receive loop in its own thread
                recv_thread = threading.Thread(
                    target=self._recv_loop,
                    args=(ws,),
                    daemon=True,
                    name="openai-recv",
                )
                recv_thread.start()

                # Send loop blocks here until disconnect() is called
                self._send_loop(ws)

                # Graceful close
                try:
                    ws.close()
                except Exception:
                    pass
                recv_thread.join(timeout=3)

        except Exception as exc:
            msg = str(exc)
            if "401" in msg or "unauthorized" in msg.lower():
                self._emit_error(
                    "OpenAI authentication failed — check your OPENAI_API_KEY."
                )
            elif "403" in msg:
                print(msg)
                self._emit_error(
                    "OpenAI access denied — your key may lack Realtime API access."
                )
            elif self._running:
                self._emit_error(f"OpenAI connection error: {exc}")
            log.error("OpenAI WebSocket error: %s", exc)
        finally:
            self._emit_status("Disconnected")
            log.info("OpenAI: WebSocket closed")

    # ------------------------------------------------------------------
    # Send / receive loops
    # ------------------------------------------------------------------

    def _send_loop(self, ws) -> None:
        """Resample, base64-encode, and forward audio chunks to WebSocket."""
        while self._running:
            try:
                audio = self._audio_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if audio is None:  # sentinel
                break
            if self._paused:
                continue
            try:
                resampled = _resample_16k_to_24k(audio)
                encoded = base64.b64encode(resampled).decode("ascii")
                ws.send(
                    json.dumps(
                        {
                            "type": "input_audio_buffer.append",
                            "audio": encoded,
                        }
                    )
                )
            except Exception as exc:
                self._emit_error(f"OpenAI audio send error: {exc}")
                log.error("OpenAI send error: %s", exc)
                break

    def _recv_loop(self, ws) -> None:
        """Iterate over server-sent frames and dispatch to _handle_event."""
        try:
            for raw in ws:
                if not self._running:
                    break
                if isinstance(raw, bytes):
                    continue  # binary frames not expected; skip
                try:
                    event = json.loads(raw)
                    self._handle_event(event)
                except json.JSONDecodeError:
                    log.warning("OpenAI: received non-JSON frame")
        except Exception as exc:
            if self._running:
                self._emit_error(f"OpenAI receive error: {exc}")
            log.debug("OpenAI recv loop exited: %s", exc)

    # ------------------------------------------------------------------
    # Event handling
    # ------------------------------------------------------------------

    def _handle_event(self, event: dict) -> None:
        etype = event.get("type", "")

        # ---- Session / connection events --------------------------------
        if etype in ("transcription_session.created", "session.created"):
            log.info("OpenAI: session created")
            self._emit_status("Live")

        elif etype in ("transcription_session.updated", "session.updated"):
            log.debug("OpenAI: session updated")

        # ---- Speech / VAD events ----------------------------------------
        elif etype == "input_audio_buffer.speech_started":
            self._speech_start = time.monotonic()
            self._interim_text = ""
            log.debug("OpenAI: speech started")

        elif etype == "input_audio_buffer.speech_stopped":
            log.debug("OpenAI: speech stopped")

        # ---- Interim transcription delta --------------------------------
        elif etype == "conversation.item.input_audio_transcription.delta":
            delta = event.get("delta", "")
            if delta:
                self._interim_text += delta
                now = time.monotonic()
                seg = TranscriptSegment(
                    start=self._speech_start - self._session_start,
                    end=now - self._session_start,
                    text=self._interim_text,
                    final=False,
                )
                if self._transcript_callback:
                    self._transcript_callback(seg)

        # ---- Final transcription ----------------------------------------
        elif etype == "conversation.item.input_audio_transcription.completed":
            transcript = event.get("transcript", "").strip()
            if not transcript:
                return
            now = time.monotonic()
            seg = TranscriptSegment(
                start=self._speech_start - self._session_start,
                end=now - self._session_start,
                text=transcript,
                final=True,
            )
            self._interim_text = ""
            log.debug("OpenAI: transcription completed (%d chars)", len(transcript))
            if self._transcript_callback:
                self._transcript_callback(seg)

        # ---- Errors -----------------------------------------------------
        elif etype == "error":
            err = event.get("error", {})
            code = err.get("code", "unknown")
            typ = err.get("type", "")
            msg = err.get("message", str(event))
            log.error("OpenAI error  type=%s  code=%s  message=%s", typ, code, msg)
            if code in ("invalid_api_key", "unauthorized") or "auth" in msg.lower():
                self._emit_error(
                    "OpenAI authentication failed — check your OPENAI_API_KEY."
                )
            elif code == "invalid_model":
                self._emit_error(
                    f"OpenAI rejected the model '{self.MODEL}' at {self.WS_URL}.\n"
                    "Check that your account has access to the Realtime transcription API."
                )
            else:
                self._emit_error(f"OpenAI error [{code}]: {msg}")

    # ------------------------------------------------------------------
    # Internal helpers  (same pattern as DeepgramEngine)
    # ------------------------------------------------------------------

    def _emit_status(self, status: str) -> None:
        if self._status_callback:
            try:
                self._status_callback(status)
            except Exception:
                pass

    def _emit_error(self, message: str) -> None:
        if self._error_callback:
            try:
                self._error_callback(message)
            except Exception:
                pass
