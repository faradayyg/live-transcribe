"""Audio capture using sounddevice — streams PCM mono 16-bit at 16 kHz."""

import queue
import threading
from typing import Callable, Optional

import numpy as np
import sounddevice as sd


class AudioCapture:
    SAMPLE_RATE = 16_000
    CHANNELS = 1
    DTYPE = "int16"
    BLOCKSIZE = 4096  # ~256 ms at 16 kHz

    def __init__(self) -> None:
        self._stream: Optional[sd.InputStream] = None
        self._audio_queue: queue.Queue[bytes] = queue.Queue()
        self._level_callback: Optional[Callable[[float], None]] = None
        self._device_index: Optional[int] = None
        self._running = False

    # ------------------------------------------------------------------
    # Device enumeration
    # ------------------------------------------------------------------

    @staticmethod
    def list_devices() -> list[dict]:
        """Return a list of available input devices as {index, name}."""
        devices = []
        try:
            for i, d in enumerate(sd.query_devices()):
                if d["max_input_channels"] > 0:
                    devices.append({"index": i, "name": d["name"]})
        except Exception:
            pass
        return devices

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def set_device(self, device_index: int) -> None:
        self._device_index = device_index

    def set_level_callback(self, callback: Callable[[float], None]) -> None:
        self._level_callback = callback

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._stream is not None:
            self.stop()

        self._running = True
        level_cb = self._level_callback

        def _callback(indata: np.ndarray, frames: int, time, status) -> None:
            if not self._running:
                return
            self._audio_queue.put(indata.copy().tobytes())
            if level_cb:
                level = float(np.abs(indata).mean()) / 32_768.0
                level_cb(min(level, 1.0))

        self._stream = sd.InputStream(
            samplerate=self.SAMPLE_RATE,
            channels=self.CHANNELS,
            dtype=self.DTYPE,
            blocksize=self.BLOCKSIZE,
            device=self._device_index,
            callback=_callback,
        )
        self._stream.start()

    def stop(self) -> None:
        self._running = False
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        self._drain_queue()

    # ------------------------------------------------------------------
    # Audio retrieval
    # ------------------------------------------------------------------

    def get_audio(self, timeout: float = 0.1) -> Optional[bytes]:
        """Block up to *timeout* seconds waiting for audio data."""
        try:
            return self._audio_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def _drain_queue(self) -> None:
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except queue.Empty:
                break
