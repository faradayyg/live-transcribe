"""
Main application window for Live Transcriber.

Layout
------
Left panel   : audio device selection, session controls, audio level meter
Center panel : live transcript display (finalized + interim)
Bottom panel : Bible reference display
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional
import logging

from PySide6.QtCore import (
    QObject,
    QTimer,
    QThread,
    Qt,
    Signal,
    Slot,
)
from PySide6.QtGui import QColor, QFont, QTextCharFormat
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from audio.capture import AudioCapture
from bible.config import (
    BIBLE_CONTEXT_SECONDS,
    BIBLE_LLM_ENABLED,
    BIBLE_REFERENCE_CONFIDENCE_THRESHOLD,
)
from bible.context import ReferenceHistory, TranscriptContextBuffer
from bible import detector as bible_detector
from bible import resolver as bible_resolver
from bible.resolver import BibleResolverWorker
from transcript.manager import TranscriptManager
from transcript.models import BibleReference, TranscriptSegment
from transcript.srt import generate_srt
from transcription import create_engine
from transcription.engine import TranscriptionEngine
from web.server import WebOutputServer

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Worker — runs in a dedicated QThread
# ---------------------------------------------------------------------------


class TranscriptionWorker(QObject):
    """Owns the audio capture and transcription engine. Lives in a worker thread."""

    transcript_received = Signal(object)  # TranscriptSegment
    status_changed = Signal(str)
    error_occurred = Signal(str)
    audio_level_changed = Signal(float)

    def __init__(self, engine: TranscriptionEngine) -> None:
        super().__init__()
        self._capture = AudioCapture()
        self._engine = engine
        self._running = False
        self._paused = False
        self._device_index: Optional[int] = None

        # Wire engine callbacks → Qt signals (called from engine's thread)
        self._engine.set_transcript_callback(
            lambda seg: self.transcript_received.emit(seg)
        )
        self._engine.set_error_callback(lambda msg: self.error_occurred.emit(msg))
        self._engine.set_status_callback(lambda s: self.status_changed.emit(s))
        self._capture.set_level_callback(lambda lvl: self.audio_level_changed.emit(lvl))

    # ------------------------------------------------------------------
    # Slots — called from the main thread via QMetaObject or direct call
    # ------------------------------------------------------------------

    @Slot(int)
    def set_device(self, index: int) -> None:
        self._device_index = index
        self._capture.set_device(index)

    @Slot()
    def start(self) -> None:
        self._running = True
        self._paused = False
        try:
            # Engine manages its own WebSocket thread; we just feed it audio.
            self._engine.connect()
            self._capture.start()
            self._feed_loop()
        except Exception as exc:
            self.error_occurred.emit(f"Worker error: {exc}")
        finally:
            self._capture.stop()

    @Slot()
    def pause(self) -> None:
        self._paused = True
        self._engine.pause()

    @Slot()
    def resume(self) -> None:
        self._paused = False
        self._engine.resume()

    @Slot()
    def stop(self) -> None:
        self.stop_sync()

    def stop_sync(self) -> None:
        """
        Thread-safe stop that can be called directly from any thread.

        Setting a plain bool is atomic under CPython's GIL, so the feed
        loop sees _running=False on its next 50 ms iteration without
        needing the QThread event loop to deliver a queued signal.
        """
        self._running = False
        self._capture.stop()  # closes the sounddevice stream
        self._engine.disconnect()  # puts sentinel in the engine's queue

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _feed_loop(self) -> None:
        """Read captured audio and forward to the engine until stopped."""
        while self._running:
            audio = self._capture.get_audio(timeout=0.05)
            if audio:
                self._engine.send_audio(audio)


# ---------------------------------------------------------------------------
# Status badge colours
# ---------------------------------------------------------------------------

_STATUS_COLORS = {
    "Disconnected": "#888888",
    "Connecting": "#e6a817",
    "Live": "#27ae60",
    "Error": "#c0392b",
    "Paused": "#2980b9",
}


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------


class MainWindow(QMainWindow):
    # Signals used to safely call worker slots from the main thread
    _sig_set_device = Signal(int)
    _sig_start = Signal()
    _sig_pause = Signal()
    _sig_resume = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Live Transcriber")
        self.setMinimumSize(1000, 680)

        self._manager = TranscriptManager()
        self._bible_context = TranscriptContextBuffer(
            window_seconds=BIBLE_CONTEXT_SECONDS
        )
        self._bible_history = ReferenceHistory()
        self._selected_ref: Optional[BibleReference] = None
        self._worker: Optional[TranscriptionWorker] = None
        self._thread: Optional[QThread] = None
        self._session_active = False
        self._paused = False
        self._selected_provider = "Deepgram"  # default

        logger.info(
            "Starting transcriber...", extra={"provider": self._selected_provider}
        )

        # LLM Bible-reference resolver (lives in the main thread; uses a
        # background thread-pool for the actual API call)
        self._resolver_worker = BibleResolverWorker()
        self._resolver_worker.refs_resolved.connect(self._on_refs_resolved)

        # Start the web output server (runs in its own background thread)
        self._web_server = WebOutputServer()
        try:
            self._web_server.start()
        except Exception as exc:
            self._web_server = None  # type: ignore[assignment]
            import logging

            logging.getLogger(__name__).warning("Web server failed to start: %s", exc)

        self._build_ui()
        self._refresh_devices()
        self._check_api_key()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # ---- Left panel ------------------------------------------------
        left = QWidget()
        left.setFixedWidth(240)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)

        # Header
        title = QLabel("Live Transcriber")
        title_font = QFont()
        title_font.setPointSize(15)
        title_font.setBold(True)
        title.setFont(title_font)
        left_layout.addWidget(title)

        self._status_badge = QLabel("● Disconnected")
        self._status_badge.setStyleSheet(
            f"color: {_STATUS_COLORS['Disconnected']}; font-weight: bold;"
        )
        left_layout.addWidget(self._status_badge)

        left_layout.addWidget(_hline())

        # Audio input group
        audio_group = QGroupBox("Audio Input")
        audio_layout = QVBoxLayout(audio_group)

        device_row = QHBoxLayout()
        self._device_combo = QComboBox()
        self._device_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._refresh_btn = QPushButton("↺")
        self._refresh_btn.setFixedWidth(30)
        self._refresh_btn.setToolTip("Refresh device list")
        self._refresh_btn.clicked.connect(self._refresh_devices)
        device_row.addWidget(self._device_combo)
        device_row.addWidget(self._refresh_btn)
        audio_layout.addLayout(device_row)

        level_label = QLabel("Level")
        audio_layout.addWidget(level_label)
        self._level_bar = QProgressBar()
        self._level_bar.setRange(0, 100)
        self._level_bar.setValue(0)
        self._level_bar.setTextVisible(False)
        self._level_bar.setFixedHeight(12)
        self._level_bar.setStyleSheet(
            "QProgressBar { border: 1px solid #555; border-radius: 3px; background: #222; }"
            "QProgressBar::chunk { background: qlineargradient(x1:0, y1:0, x2:1, y2:0,"
            " stop:0 #27ae60, stop:0.6 #f39c12, stop:1 #e74c3c); border-radius: 2px; }"
        )
        audio_layout.addWidget(self._level_bar)
        left_layout.addWidget(audio_group)

        # Session group
        session_group = QGroupBox("Session")
        session_layout = QVBoxLayout(session_group)

        session_layout.addWidget(QLabel("Transcription engine:"))
        self._engine_combo = QComboBox()
        self._engine_combo.addItems(["Deepgram", "OpenAI"])
        self._engine_combo.currentTextChanged.connect(self._on_engine_changed)
        session_layout.addWidget(self._engine_combo)

        session_layout.addWidget(QLabel("Session name:"))
        self._session_name = QLineEdit("session")
        session_layout.addWidget(self._session_name)

        self._start_btn = QPushButton("▶  Start")
        self._start_btn.setStyleSheet(
            "background-color: #27ae60; color: white; font-weight: bold; padding: 6px;"
        )
        self._start_btn.clicked.connect(self._on_start)
        session_layout.addWidget(self._start_btn)

        self._pause_btn = QPushButton("⏸  Pause")
        self._pause_btn.setEnabled(False)
        self._pause_btn.clicked.connect(self._on_pause)
        session_layout.addWidget(self._pause_btn)

        self._stop_btn = QPushButton("⏹  Stop")
        self._stop_btn.setEnabled(False)
        self._stop_btn.setStyleSheet(
            "background-color: #c0392b; color: white; padding: 6px;"
        )
        self._stop_btn.clicked.connect(self._on_stop)
        session_layout.addWidget(self._stop_btn)

        self._save_btn = QPushButton("💾  Save Transcript")
        self._save_btn.setEnabled(False)
        self._save_btn.clicked.connect(self._on_save)
        session_layout.addWidget(self._save_btn)

        left_layout.addWidget(session_group)

        # ---- Web output panel -----------------------------------------
        web_group = QGroupBox("Web Output")
        web_layout = QVBoxLayout(web_group)
        web_layout.setSpacing(4)

        if self._web_server:
            base_url = self._web_server.url
            full_url = base_url + "?full=true"
            bible_url = base_url + "?bible=true"

            label_style = "color: #6ab0f5; font-size: 11px;"
            selectable = Qt.TextInteractionFlag.TextSelectableByMouse

            web_layout.addWidget(QLabel("Lower-third (default):"))
            lt_row = QHBoxLayout()
            lt_label = QLabel(base_url)
            lt_label.setStyleSheet(label_style)
            lt_label.setWordWrap(True)
            lt_label.setTextInteractionFlags(selectable)
            copy_lt_btn = QPushButton("⧉")
            copy_lt_btn.setFixedWidth(28)
            copy_lt_btn.setToolTip("Copy lower-third URL")
            copy_lt_btn.clicked.connect(
                lambda: self._copy_to_clipboard(base_url, copy_lt_btn)
            )
            lt_row.addWidget(lt_label)
            lt_row.addWidget(copy_lt_btn)
            web_layout.addLayout(lt_row)

            web_layout.addWidget(QLabel("Full transcript:"))
            full_row = QHBoxLayout()
            full_label = QLabel(full_url)
            full_label.setStyleSheet(label_style)
            full_label.setWordWrap(True)
            full_label.setTextInteractionFlags(selectable)
            copy_full_btn = QPushButton("⧉")
            copy_full_btn.setFixedWidth(28)
            copy_full_btn.setToolTip("Copy full-transcript URL")
            copy_full_btn.clicked.connect(
                lambda: self._copy_to_clipboard(full_url, copy_full_btn)
            )
            full_row.addWidget(full_label)
            full_row.addWidget(copy_full_btn)
            web_layout.addLayout(full_row)

            web_layout.addWidget(QLabel("Bible-only:"))
            bible_row = QHBoxLayout()
            bible_label = QLabel(bible_url)
            bible_label.setStyleSheet(label_style)
            bible_label.setWordWrap(True)
            bible_label.setTextInteractionFlags(selectable)
            copy_bible_btn = QPushButton("⧉")
            copy_bible_btn.setFixedWidth(28)
            copy_bible_btn.setToolTip("Copy bible-only URL")
            copy_bible_btn.clicked.connect(
                lambda: self._copy_to_clipboard(bible_url, copy_bible_btn)
            )
            bible_row.addWidget(bible_label)
            bible_row.addWidget(copy_bible_btn)
            web_layout.addLayout(bible_row)

            open_lt_btn = QPushButton("🌐  Open Lower-third")
            open_full_btn = QPushButton("🌐  Open Full Transcript")
            open_bible_btn = QPushButton("🌐  Open Bible-only")
            open_lt_btn.clicked.connect(self._open_web_output)
            open_full_btn.clicked.connect(self._open_web_output_full)
            open_bible_btn.clicked.connect(self._open_web_output_bible)
            web_layout.addWidget(open_lt_btn)
            web_layout.addWidget(open_full_btn)
            web_layout.addWidget(open_bible_btn)
        else:
            web_layout.addWidget(QLabel("⚠ Server unavailable"))

        left_layout.addWidget(web_group)
        left_layout.addStretch()

        # ---- Right side (transcript + bible) ---------------------------
        right_splitter = QSplitter(Qt.Vertical)

        transcript_frame = QGroupBox("Live Transcript")
        transcript_layout = QVBoxLayout(transcript_frame)
        self._transcript_view = QTextEdit()
        self._transcript_view.setReadOnly(True)
        self._transcript_view.setFont(QFont("Georgia", 13))
        self._transcript_view.setStyleSheet(
            "QTextEdit { background: #1a1a2e; color: #e0e0e0; border: none; padding: 8px; }"
        )
        transcript_layout.addWidget(self._transcript_view)
        right_splitter.addWidget(transcript_frame)

        bible_frame = QGroupBox("Scripture")
        bible_layout = QVBoxLayout(bible_frame)

        # ---- Manual reference lookup --------------------------------
        lookup_row = QHBoxLayout()
        lookup_row.addWidget(QLabel("Reference:"))
        self._ref_input = QLineEdit()
        self._ref_input.setPlaceholderText("e.g.  John 3:16  or  Romans 8:1-4")
        self._ref_input.returnPressed.connect(self._on_manual_ref_display)
        lookup_row.addWidget(self._ref_input)
        display_btn = QPushButton("Display")
        display_btn.setFixedWidth(64)
        display_btn.clicked.connect(self._on_manual_ref_display)
        lookup_row.addWidget(display_btn)
        self._ref_error_label = QLabel("")
        self._ref_error_label.setStyleSheet("color: #e05050; font-size: 11px;")
        bible_layout.addLayout(lookup_row)
        bible_layout.addWidget(self._ref_error_label)

        bible_layout.addWidget(_hline())

        list_header = QHBoxLayout()
        list_header.addWidget(QLabel("References detected this session:"))
        list_header.addStretch()
        clear_bible_btn = QPushButton("✕  Clear")
        clear_bible_btn.setFixedHeight(22)
        clear_bible_btn.setStyleSheet("font-size: 11px; padding: 0 6px;")
        clear_bible_btn.clicked.connect(self._clear_bible_ref)
        list_header.addWidget(clear_bible_btn)
        bible_layout.addLayout(list_header)

        self._bible_list = QListWidget()
        self._bible_list.setFixedHeight(110)
        self._bible_list.setStyleSheet(
            "QListWidget { background: #1a1a2e; color: #e0e0e0; border: 1px solid #444; font-size: 12px; }"
            "QListWidget::item:selected { background: #2c5f8a; color: #ffffff; }"
        )
        self._bible_list.itemClicked.connect(self._on_bible_item_clicked)
        bible_layout.addWidget(self._bible_list)

        bible_layout.addWidget(_hline())
        self._bible_ref_label = QLabel("—")
        ref_font = QFont()
        ref_font.setPointSize(12)
        ref_font.setBold(True)
        self._bible_ref_label.setFont(ref_font)
        bible_layout.addWidget(self._bible_ref_label)

        # Verse-pair navigator — shown only for ranged references
        self._chunk_label = QLabel("Click a pair to display on overlay:")
        self._chunk_label.setStyleSheet("color: #888; font-size: 11px; margin-top: 4px;")
        self._chunk_list = QListWidget()
        self._chunk_list.setFixedHeight(72)
        self._chunk_list.setFlow(QListWidget.Flow.LeftToRight)
        self._chunk_list.setWrapping(True)
        self._chunk_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self._chunk_list.setStyleSheet(
            "QListWidget { background: #111122; color: #e0e0e0; border: 1px solid #333; font-size: 11px; }"
            "QListWidget::item { padding: 3px 8px; margin: 2px; border: 1px solid #444; border-radius: 4px; }"
            "QListWidget::item:selected { background: #2c5f8a; color: #fff; border-color: #5599cc; }"
        )
        self._chunk_list.itemClicked.connect(self._on_chunk_clicked)
        self._chunk_label.hide()
        self._chunk_list.hide()
        bible_layout.addWidget(self._chunk_label)
        bible_layout.addWidget(self._chunk_list)

        self._bible_verse_label = QLabel("")
        self._bible_verse_label.setWordWrap(True)
        self._bible_verse_label.setStyleSheet("color: #aaa; font-style: italic;")
        bible_layout.addWidget(self._bible_verse_label)
        bible_layout.addStretch()
        right_splitter.addWidget(bible_frame)

        right_splitter.setSizes([500, 140])

        # ---- API key warning -------------------------------------------
        self._api_warning = QLabel()
        self._api_warning.setWordWrap(True)
        self._api_warning.setStyleSheet(
            "background: #4a1010; color: #ffaaaa; padding: 6px; border-radius: 4px;"
        )
        self._api_warning.setVisible(False)

        right_wrapper = QWidget()
        rw_layout = QVBoxLayout(right_wrapper)
        rw_layout.setContentsMargins(0, 0, 0, 0)
        rw_layout.addWidget(self._api_warning)
        rw_layout.addWidget(right_splitter)

        # ---- Assemble --------------------------------------------------
        root.addWidget(left)
        root.addWidget(right_wrapper, stretch=1)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _check_api_key(self) -> None:
        provider = self._selected_provider
        if (
            provider == "Deepgram"
            and not os.environ.get("DEEPGRAM_API_KEY", "").strip()
        ):
            self._api_warning.setText(
                "⚠  DEEPGRAM_API_KEY is not set.  "
                "Export it before starting:\n"
                "    export DEEPGRAM_API_KEY=your_key_here"
            )
            self._api_warning.setVisible(True)
        elif provider == "OpenAI" and not os.environ.get("OPENAI_API_KEY", "").strip():
            self._api_warning.setText(
                "⚠  OPENAI_API_KEY is not set.  "
                "Export it before starting:\n"
                "    export OPENAI_API_KEY=your_key_here"
            )
            self._api_warning.setVisible(True)
        else:
            self._api_warning.setVisible(False)

    @Slot(str)
    def _on_engine_changed(self, provider: str) -> None:
        self._selected_provider = provider
        self._check_api_key()

    @Slot()
    def _open_web_output(self) -> None:
        import webbrowser

        if self._web_server:
            webbrowser.open(self._web_server.url)

    @Slot()
    def _open_web_output_full(self) -> None:
        import webbrowser

        if self._web_server:
            webbrowser.open(self._web_server.url + "?full=true")

    @Slot()
    def _open_web_output_bible(self) -> None:
        import webbrowser

        if self._web_server:
            webbrowser.open(self._web_server.url + "?bible=true")

    def _copy_to_clipboard(self, text: str, button: QPushButton) -> None:
        QApplication.clipboard().setText(text)
        original = button.text()
        button.setText("✓")
        QTimer.singleShot(1500, lambda: button.setText(original))

    @Slot()
    def _refresh_devices(self) -> None:
        devices = AudioCapture.list_devices()
        self._device_combo.clear()
        if not devices:
            self._device_combo.addItem("No input devices found", userData=-1)
            return
        for d in devices:
            self._device_combo.addItem(d["name"], userData=d["index"])

    def _set_status(self, status: str) -> None:
        color = _STATUS_COLORS.get(status, "#888888")
        provider = self._selected_provider
        self._status_badge.setText(f"● {status}  [{provider}]")
        self._status_badge.setStyleSheet(f"color: {color}; font-weight: bold;")
        if self._web_server:
            self._web_server.broadcast_status(status)

    # ------------------------------------------------------------------
    # Transcript display
    # ------------------------------------------------------------------

    def _rebuild_transcript(self) -> None:
        """Repaint the transcript view from the manager's current state."""
        self._transcript_view.clear()
        cursor = self._transcript_view.textCursor()

        # Finalized segments — plain white
        final_fmt = QTextCharFormat()
        final_fmt.setForeground(QColor("#e0e0e0"))

        for seg in self._manager.get_segments():
            text = seg.text.strip()
            if text:
                cursor.insertText(text + "\n\n", final_fmt)

        # Interim segment — muted italic colour
        interim = self._manager.interim
        if interim and interim.text.strip():
            interim_fmt = QTextCharFormat()
            interim_fmt.setForeground(QColor("#7f8c8d"))
            interim_fmt.setFontItalic(True)
            cursor.insertText(interim.text.strip(), interim_fmt)

        # Scroll to bottom
        self._transcript_view.verticalScrollBar().setValue(
            self._transcript_view.verticalScrollBar().maximum()
        )

    # ------------------------------------------------------------------
    # Session controls
    # ------------------------------------------------------------------

    @Slot()
    def _on_start(self) -> None:
        name = self._session_name.text().strip()
        logger.info(
            "Starting session: %s", name, extra={"provider": self._selected_provider}
        )
        if not name:
            QMessageBox.warning(
                self, "Invalid Session Name", "Please enter a session name."
            )
            return

        device_index = self._device_combo.currentData()
        if device_index is None or device_index == -1:
            QMessageBox.warning(
                self, "No Device", "Please select an audio input device."
            )
            return

        self._manager.clear()
        self._clear_bible_ref()
        self._transcript_view.clear()
        self._session_active = True
        self._paused = False

        self._start_btn.setEnabled(False)
        self._pause_btn.setEnabled(True)
        self._stop_btn.setEnabled(True)
        self._save_btn.setEnabled(False)
        self._device_combo.setEnabled(False)
        self._refresh_btn.setEnabled(False)
        self._engine_combo.setEnabled(False)

        self._start_worker(device_index)

    def _start_worker(self, device_index: int) -> None:
        self._thread = QThread()
        engine = create_engine(self._selected_provider)
        self._worker = TranscriptionWorker(engine)
        self._worker.moveToThread(self._thread)

        # Connect signals
        self._worker.transcript_received.connect(self._on_transcript)
        self._worker.status_changed.connect(self._set_status)
        self._worker.error_occurred.connect(self._on_error)
        self._worker.audio_level_changed.connect(self._on_level)

        # Wire control signals to worker slots (cross-thread safe)
        self._sig_set_device.connect(self._worker.set_device)
        self._sig_start.connect(self._worker.start)
        self._sig_pause.connect(self._worker.pause)
        self._sig_resume.connect(self._worker.resume)

        self._thread.start()
        self._set_status("Connecting")
        self._sig_set_device.emit(device_index)
        self._sig_start.emit()

    @Slot()
    def _on_pause(self) -> None:
        if not self._paused:
            self._paused = True
            self._sig_pause.emit()
            self._pause_btn.setText("▶  Resume")
            self._set_status("Paused")
        else:
            self._paused = False
            self._sig_resume.emit()
            self._pause_btn.setText("⏸  Pause")
            self._set_status("Live")

    @Slot()
    def _on_stop(self) -> None:
        self._session_active = False
        self._stop_worker()

        self._start_btn.setEnabled(True)
        self._pause_btn.setEnabled(False)
        self._pause_btn.setText("⏸  Pause")
        self._stop_btn.setEnabled(False)
        self._device_combo.setEnabled(True)
        self._refresh_btn.setEnabled(True)
        self._engine_combo.setEnabled(True)
        self._level_bar.setValue(0)

        has_content = bool(self._manager.get_segments())
        self._save_btn.setEnabled(has_content)
        self._set_status("Disconnected")

    def _stop_worker(self) -> None:
        if self._worker:
            # Call stop_sync() directly — bypasses the QThread event loop so
            # the blocking _feed_loop sees _running=False within one iteration.
            self._worker.stop_sync()
        if self._thread and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(3000)
        self._worker = None
        self._thread = None

    @Slot()
    def _on_save(self) -> None:
        name = self._session_name.text().strip() or "session"
        save_dir = QFileDialog.getExistingDirectory(
            self, "Choose Save Directory", str(Path.home())
        )
        if not save_dir:
            return
        self._save_files(Path(save_dir), name)

    def _save_files(self, directory: Path, name: str) -> None:
        errors: list[str] = []
        segments = self._manager.get_segments()

        # Plain text
        txt_path = directory / f"{name}.txt"
        try:
            txt_path.write_text(self._manager.get_final_text(), encoding="utf-8")
        except Exception as exc:
            errors.append(f"TXT: {exc}")

        # SRT
        srt_path = directory / f"{name}.srt"
        try:
            srt_path.write_text(generate_srt(segments), encoding="utf-8")
        except Exception as exc:
            errors.append(f"SRT: {exc}")

        if errors:
            QMessageBox.warning(self, "Save Error", "\n".join(errors))
        else:
            QMessageBox.information(
                self,
                "Saved",
                f"Transcript saved to:\n  {txt_path}\n  {srt_path}",
            )

    # ------------------------------------------------------------------
    # Worker signal handlers
    # ------------------------------------------------------------------

    @Slot(object)
    def _on_transcript(self, segment: TranscriptSegment) -> None:
        if segment.final:
            self._manager.add_final(segment)
            self._manager.clear_interim()
            # Bible detection on rolling context — non-blocking, best-effort
            try:
                context_text = self._bible_context.add_segment(segment)
                if bible_resolver.is_candidate(context_text):
                    logger.info("Bible candidate detected: %s", context_text)
                    if BIBLE_LLM_ENABLED:
                        # Async path: debounce → background LLM → _on_refs_resolved
                        self._resolver_worker.schedule(context_text, self._selected_ref)
                    else:
                        # Synchronous fallback: local regex detector
                        refs = bible_detector.detect_all(context_text)
                        for ref in refs:
                            ref.source_text = context_text
                        self._process_detected_refs(refs)
            except Exception:
                pass
        else:
            self._manager.update_interim(segment)

        self._rebuild_transcript()

        # Mirror every segment to the web output (non-blocking)
        if self._web_server:
            self._web_server.broadcast_transcript(segment)

    @Slot(list)
    def _on_refs_resolved(self, refs: list) -> None:
        """Slot — called on the main thread after the LLM resolver completes."""
        try:
            self._process_detected_refs(refs)
        except Exception:
            pass

    def _process_detected_refs(self, refs: list) -> None:
        """Update history and UI for a list of BibleReference objects."""
        threshold = BIBLE_REFERENCE_CONFIDENCE_THRESHOLD
        for ref in refs:
            if ref.confidence < threshold:
                continue
            result = self._bible_history.add_or_upgrade(ref)
            if result in ("added", "upgraded"):
                self._refresh_bible_list()
                if result == "added":
                    self._show_bible_ref(ref, auto_select=True)
                elif result == "upgraded":
                    if (
                        self._selected_ref
                        and self._selected_ref.book == ref.book
                        and self._selected_ref.chapter == ref.chapter
                    ):
                        self._show_bible_ref(ref, auto_select=True)

    @Slot(str)
    def _on_error(self, message: str) -> None:
        self._set_status("Error")
        self._transcript_view.append(f"\n⚠ {message}\n")

    @Slot(float)
    def _on_level(self, level: float) -> None:
        self._level_bar.setValue(int(level * 100))

    def _refresh_bible_list(self) -> None:
        """Rebuild the history list widget from current ReferenceHistory."""
        self._bible_list.blockSignals(True)
        self._bible_list.clear()
        for ref in self._bible_history.get_all():
            item = QListWidgetItem(ref.display())
            item.setData(Qt.ItemDataRole.UserRole, ref)
            self._bible_list.addItem(item)
        if self._selected_ref:
            sel_key = self._selected_ref.normalized_key()
            for i in range(self._bible_list.count()):
                item = self._bible_list.item(i)
                ref = item.data(Qt.ItemDataRole.UserRole)
                if ref and ref.normalized_key() == sel_key:
                    self._bible_list.setCurrentItem(item)
                    break
        self._bible_list.blockSignals(False)

    @Slot(object)
    def _on_bible_item_clicked(self, item: QListWidgetItem) -> None:
        ref = item.data(Qt.ItemDataRole.UserRole)
        if ref:
            self._show_bible_ref(ref, auto_select=False)

    @Slot()
    def _on_manual_ref_display(self) -> None:
        """Parse the typed reference and display it on the overlay."""
        text = self._ref_input.text().strip()
        if not text:
            return
        refs = bible_detector.detect_all(text)
        if not refs:
            self._ref_error_label.setText(f'Could not parse "{text}"')
            return
        self._ref_error_label.setText("")
        ref = refs[0]
        # Add to session history so it appears in the detected list
        self._bible_history.add_or_upgrade(ref)
        self._show_bible_ref(ref, auto_select=True)

    def _show_bible_ref(self, ref: BibleReference, *, auto_select: bool = True) -> None:
        self._selected_ref = ref
        self._bible_ref_label.setText(ref.display())
        self._populate_chunk_list(ref)
        # Default verse text: single verse or all verses in range
        verse_text = self._get_verse_range_text(ref)
        self._bible_verse_label.setText(verse_text)
        if auto_select:
            self._refresh_bible_list()
        if self._web_server:
            self._web_server.broadcast_bible(ref.display(), verse_text)

    def _populate_chunk_list(self, ref: BibleReference) -> None:
        """Populate the verse-pair navigator when ref spans multiple verses."""
        self._chunk_list.clear()
        if ref.verse_start is None or ref.verse_end is None:
            self._chunk_label.hide()
            self._chunk_list.hide()
            return

        chunks = self._split_into_pairs(ref)
        if len(chunks) <= 1:
            # Single verse or single pair — no navigator needed
            self._chunk_label.hide()
            self._chunk_list.hide()
            return

        self._chunk_label.show()
        self._chunk_list.show()
        for chunk in chunks:
            item = QListWidgetItem(chunk.display())
            item.setData(Qt.ItemDataRole.UserRole, chunk)
            self._chunk_list.addItem(item)

    @staticmethod
    def _split_into_pairs(ref: BibleReference) -> list[BibleReference]:
        """Split a ranged reference into 2-verse chunks."""
        from dataclasses import replace
        vs = ref.verse_start
        ve = ref.verse_end
        chunks: list[BibleReference] = []
        v = vs
        while v <= ve:
            pair_end = min(v + 1, ve)
            end = None if pair_end == v else pair_end
            chunks.append(replace(ref, verse_start=v, verse_end=end, confidence=1.0))
            v += 2
        return chunks

    @Slot(QListWidgetItem)
    def _on_chunk_clicked(self, item: QListWidgetItem) -> None:
        chunk: BibleReference = item.data(Qt.ItemDataRole.UserRole)
        verse_text = self._get_verse_range_text(chunk)
        self._bible_verse_label.setText(verse_text)
        if self._web_server:
            self._web_server.broadcast_bible(chunk.display(), verse_text)

    def _get_verse_range_text(self, ref: BibleReference) -> str:
        if ref.verse_start is None:
            return ""
        if ref.verse_end is None:
            return (
                bible_detector.lookup_verse(ref.book, ref.chapter, ref.verse_start)
                or ""
            )
        texts: list[str] = []
        for v in range(ref.verse_start, ref.verse_end + 1):
            text = bible_detector.lookup_verse(ref.book, ref.chapter, v)
            if text:
                texts.append(f"{v}  {text}")
        return "\n".join(texts) if texts else ""

    @Slot()
    def _clear_bible_ref(self) -> None:
        self._bible_context.clear()
        self._selected_ref = None
        self._bible_list.clear()
        self._bible_ref_label.setText("—")
        self._bible_verse_label.setText("")
        self._chunk_list.clear()
        self._chunk_label.hide()
        self._chunk_list.hide()
        if self._web_server:
            self._web_server.broadcast_bible("", "")

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        # Always attempt a clean shutdown — session may be active or not.
        self._session_active = False
        self._stop_worker()
        self._resolver_worker.shutdown()
        if self._web_server:
            self._web_server.stop()
        event.accept()


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _hline() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setFrameShadow(QFrame.Sunken)
    return line
