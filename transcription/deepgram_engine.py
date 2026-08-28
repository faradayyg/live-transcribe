"""
Deepgram streaming transcription engine — Deepgram SDK v7 (Fern-generated).

The SDK v7 `client.listen.v1.connect()` is a synchronous context manager that
yields a `V1SocketClient`.  We run the WebSocket lifecycle in a background
thread so the caller never blocks.

Send path  : caller calls send_audio() → internal queue → _send_loop thread
Receive path: _recv_loop thread iterates the socket → transcript callback
"""

from __future__ import annotations

import os
import queue
import threading
from typing import Optional

from deepgram import DeepgramClient
from deepgram.core.api_error import ApiError
from deepgram.listen.v1.types.listen_v1results import ListenV1Results

from transcript.models import TranscriptSegment
from transcription.engine import TranscriptionEngine


class DeepgramEngine(TranscriptionEngine):
    """Streams audio to Deepgram's live transcription WebSocket (SDK v7)."""

    def __init__(self) -> None:
        super().__init__()
        self._client: Optional[DeepgramClient] = None
        self._audio_queue: queue.Queue[Optional[bytes]] = queue.Queue()
        self._ws_thread: Optional[threading.Thread] = None
        self._running = False
        self._paused = False

    # ------------------------------------------------------------------
    # TranscriptionEngine interface
    # ------------------------------------------------------------------

    def connect(self) -> None:
        api_key = os.environ.get("DEEPGRAM_API_KEY", "").strip()
        if not api_key:
            self._emit_error(
                "DEEPGRAM_API_KEY is not set.\n"
                "Export it in your shell before launching:\n"
                "  export DEEPGRAM_API_KEY=your_key_here"
            )
            return

        try:
            # SDK v7: keyword-only api_key parameter
            self._client = DeepgramClient(api_key=api_key)
        except Exception as exc:
            self._emit_error(f"Deepgram client initialisation failed: {exc}")
            return

        self._running = True
        self._emit_status("Connecting")
        self._ws_thread = threading.Thread(
            target=self._ws_lifecycle, daemon=True, name="deepgram-ws"
        )
        self._ws_thread.start()

    def send_audio(self, audio_bytes: bytes) -> None:
        """Queue raw PCM bytes for delivery to the WebSocket."""
        if not self._running:
            return
        self._audio_queue.put(audio_bytes)

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def disconnect(self) -> None:
        self._running = False
        # Unblock the send loop by sending a sentinel
        self._audio_queue.put(None)
        if self._ws_thread and self._ws_thread.is_alive():
            self._ws_thread.join(timeout=5)
        self._ws_thread = None

    # ------------------------------------------------------------------
    # WebSocket lifecycle (runs in _ws_thread)
    # ------------------------------------------------------------------

    def _ws_lifecycle(self) -> None:
        """Open the Deepgram WebSocket, drive send + receive, then clean up."""
        try:
            assert self._client is not None
            with self._client.listen.v1.connect(
                model="nova-2",
                language="en-US",
                encoding="linear16",
                channels=1,
                sample_rate=16_000,
                interim_results=False,
                punctuate=True,
                smart_format=True,
            ) as socket:
                self._emit_status("Live")

                # Receive loop in its own thread
                recv_thread = threading.Thread(
                    target=self._recv_loop,
                    args=(socket,),
                    daemon=True,
                    name="deepgram-recv",
                )
                recv_thread.start()

                # Send loop blocks here until disconnect() is called
                self._send_loop(socket)

                # Gracefully close the stream
                try:
                    socket.send_close_stream()
                except Exception:
                    pass

                recv_thread.join(timeout=3)

        except ApiError as exc:
            msg = (
                "Deepgram authentication failed — check your API key."
                if exc.status_code == 401
                else f"Deepgram API error {exc.status_code}: {exc.body}"
            )
            self._emit_error(msg)
        except Exception as exc:
            if self._running:  # Only report if not a deliberate disconnect
                self._emit_error(f"Deepgram connection error: {exc}")
        finally:
            self._emit_status("Disconnected")

    def _send_loop(self, socket) -> None:
        """Read from the audio queue and forward to the WebSocket."""
        while self._running:
            try:
                audio = self._audio_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if audio is None:  # sentinel — stop signal
                break
            if self._paused:
                continue
            try:
                socket.send_media(audio)
            except Exception as exc:
                self._emit_error(f"Audio send error: {exc}")
                break

    def _recv_loop(self, socket) -> None:
        """Iterate over incoming WebSocket messages and fire callbacks."""
        try:
            for message in socket:
                if not self._running:
                    break
                if isinstance(message, ListenV1Results):
                    self._handle_result(message)
        except Exception as exc:
            if self._running:
                self._emit_error(f"Receive error: {exc}")

    # ------------------------------------------------------------------
    # Result parsing
    # ------------------------------------------------------------------

    def _handle_result(self, result: ListenV1Results) -> None:
        try:
            alternatives = result.channel.alternatives
            if not alternatives:
                return
            text = alternatives[0].transcript
            if not text:
                return

            segment = TranscriptSegment(
                start=float(result.start),
                end=float(result.start) + float(result.duration),
                text=text,
                final=bool(result.is_final),
            )
            if self._transcript_callback:
                self._transcript_callback(segment)
        except Exception:
            pass  # Never let parsing crash the audio pipeline

    # ------------------------------------------------------------------
    # Internal helpers
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
