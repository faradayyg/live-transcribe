"""
Main application window for Live Transcriber.

Layout
------
Left panel   : audio device selection, session controls, audio level meter
Center panel : live transcript display (finalized + interim)
Bottom panel : Bible reference display
"""

from __future__ import annotations

import math
import os
import webbrowser
from dataclasses import replace
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
from web.server import BIBLE_STYLES, WebOutputServer

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

_VALID_DISPLAY_MODES = ("subtitles_bible", "bible_only")

_NO_REFERENCE_TEXT = "No reference selected"
_REF_LABEL_ACTIVE_STYLE = "color: #f0c04a;"
_REF_LABEL_EMPTY_STYLE = "color: #9a9aac;"

# Meter colour by position: (minimum meter value, colour), loudest first.
_LEVEL_BANDS = ((90, "#e74c3c"), (75, "#f39c12"), (0, "#27ae60"))

# Fusion + the dark palette (main.py) supply the base look; this adds the
# shared widget styling so it is defined once rather than per widget.
_APP_STYLESHEET = """
QGroupBox {
    border: 1px solid #333348; border-radius: 8px;
    margin-top: 14px; padding: 8px 4px 4px 4px;
}
QGroupBox::title {
    subcontrol-origin: margin; left: 8px; padding: 0 3px;
    color: #9a9aac; font-weight: bold;
}
QPushButton {
    background: #2d2d41; border: 1px solid #3f3f58; border-radius: 6px;
    padding: 5px 10px;
}
QPushButton:hover { background: #38384f; border-color: #4090cf; }
QPushButton:pressed { background: #26263a; }
QPushButton:disabled {
    background: #232333; border-color: #2c2c3e; color: #66667a;
}
QPushButton#startBtn:enabled {
    background: #27ae60; border-color: #27ae60; color: white; font-weight: bold;
}
QPushButton#startBtn:enabled:hover { background: #2fc06c; }
QPushButton#stopBtn:enabled {
    background: #c0392b; border-color: #c0392b; color: white; font-weight: bold;
}
QPushButton#stopBtn:enabled:hover { background: #d44637; }
QPushButton#smallBtn { padding: 2px 8px; font-size: 11px; }
QLineEdit {
    background: #161623; border: 1px solid #333348; border-radius: 6px;
    padding: 4px 6px;
}
QLineEdit:focus { border-color: #4090cf; }
QTextEdit {
    background: #161623; color: #e0e0e0; border: 1px solid #2a2a3c;
    border-radius: 6px; padding: 8px;
}
QListWidget {
    background: #161623; color: #e0e0e0; border: 1px solid #2a2a3c;
    border-radius: 6px; font-size: 12px; outline: 0;
}
QListWidget::item { padding: 4px 8px; border-radius: 4px; }
QListWidget::item:hover { background: #26263a; }
QListWidget::item:selected { background: #2c5f8a; color: white; }
QListWidget#chunkList::item {
    padding: 3px 10px; margin: 2px; border: 1px solid #3f3f58;
}
QListWidget#chunkList::item:selected { border-color: #4090cf; }
QProgressBar { border: none; border-radius: 3px; background: #161623; }
QLabel#banner {
    background: #4a1010; color: #ffb4b4; padding: 8px; border-radius: 6px;
}
QLabel#muted { color: #9a9aac; font-size: 11px; }
"""


# ---------------------------------------------------------------------------
# Control bridge — web control panel (aiohttp thread) -> Qt main thread
# ---------------------------------------------------------------------------


class ControlBridge(QObject):
    """
    Cross-thread bridge between the web control API and the Qt main thread.

    The aiohttp server runs in its own background thread. Its request
    handlers call `.emit()` on these signals; because this QObject is
    constructed on (and lives on) the Qt main thread, Qt automatically
    queues delivery so the connected slots always run on the main thread —
    the same pattern already used for TranscriptionEngine callbacks.
    """

    pause_requested = Signal()
    resume_requested = Signal()
    select_reference_requested = Signal(str)
    set_bible_visible_requested = Signal(bool)
    set_display_mode_requested = Signal(str)
    set_bible_style_requested = Signal(str)
    select_chunk_requested = Signal(int)
    manual_reference_requested = Signal(str)


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
        self._bible_visible = True
        self._display_mode = "subtitles_bible"
        self._bible_style = "gold"
        self._current_chunk_index: Optional[int] = None
        self._selected_provider = "Deepgram"  # default
        self._level_color = ""

        logger.info(
            "Starting transcriber...", extra={"provider": self._selected_provider}
        )

        # LLM Bible-reference resolver (lives in the main thread; uses a
        # background thread-pool for the actual API call)
        self._resolver_worker = BibleResolverWorker()
        self._resolver_worker.refs_resolved.connect(self._on_refs_resolved)

        # Control bridge: connects the web /control panel's HTTP commands to
        # the same application-level operations used by the PySide6 buttons.
        self._control_bridge = ControlBridge()
        self._control_bridge.pause_requested.connect(self._pause_transcription)
        self._control_bridge.resume_requested.connect(self._resume_transcription)
        self._control_bridge.select_reference_requested.connect(
            self._select_reference_by_key
        )
        self._control_bridge.set_bible_visible_requested.connect(
            self._set_bible_visible
        )
        self._control_bridge.set_display_mode_requested.connect(
            self._set_display_mode
        )
        self._control_bridge.set_bible_style_requested.connect(
            self._set_bible_style
        )
        self._control_bridge.select_chunk_requested.connect(
            self._select_chunk_by_index
        )
        self._control_bridge.manual_reference_requested.connect(
            self._manual_ref_from_web
        )

        # Start the web output server (runs in its own background thread)
        self._web_server = WebOutputServer()
        try:
            self._web_server.start()
        except Exception as exc:
            self._web_server = None  # type: ignore[assignment]
            import logging

            logging.getLogger(__name__).warning("Web server failed to start: %s", exc)

        if self._web_server:
            self._web_server.set_control_callbacks(
                pause=self._control_bridge.pause_requested.emit,
                resume=self._control_bridge.resume_requested.emit,
                select_reference=self._control_bridge.select_reference_requested.emit,
                set_bible_visible=self._control_bridge.set_bible_visible_requested.emit,
                set_display_mode=self._control_bridge.set_display_mode_requested.emit,
                set_bible_style=self._control_bridge.set_bible_style_requested.emit,
                select_chunk=self._control_bridge.select_chunk_requested.emit,
                manual_reference=self._control_bridge.manual_reference_requested.emit,
            )

        self._build_ui()
        self._refresh_devices()
        self._check_api_key()
        self._push_state()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.setStyleSheet(_APP_STYLESHEET)

        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        # ---- Left panel ------------------------------------------------
        left = QWidget()
        left.setFixedWidth(240)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

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

        self._level_bar = QProgressBar()
        self._level_bar.setRange(0, 100)
        self._level_bar.setValue(0)
        self._level_bar.setTextVisible(False)
        self._level_bar.setFixedHeight(10)
        self._level_bar.setToolTip("Input level")
        audio_layout.addWidget(self._level_bar)
        left_layout.addWidget(audio_group)

        # Session group
        session_group = QGroupBox("Session")
        session_layout = QVBoxLayout(session_group)

        session_layout.addWidget(_muted_label("Transcription engine"))
        self._engine_combo = QComboBox()
        self._engine_combo.addItems(["Deepgram", "OpenAI"])
        self._engine_combo.currentTextChanged.connect(self._on_engine_changed)
        session_layout.addWidget(self._engine_combo)

        session_layout.addWidget(_muted_label("Session name"))
        self._session_name = QLineEdit("session")
        session_layout.addWidget(self._session_name)

        self._start_btn = QPushButton("Start")
        self._start_btn.setObjectName("startBtn")
        self._start_btn.clicked.connect(self._on_start)
        session_layout.addWidget(self._start_btn)

        transport_row = QHBoxLayout()
        self._pause_btn = QPushButton("Pause")
        self._pause_btn.setEnabled(False)
        self._pause_btn.clicked.connect(self._on_pause)
        transport_row.addWidget(self._pause_btn)

        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setObjectName("stopBtn")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._on_stop)
        transport_row.addWidget(self._stop_btn)
        session_layout.addLayout(transport_row)

        self._save_btn = QPushButton("Save Transcript")
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
            for name, url in (
                ("Lower-third", base_url),
                ("Full transcript", base_url + "?full=true"),
                ("Bible only", base_url + "?bible=true"),
                ("Control panel", self._web_server.control_url),
            ):
                web_layout.addLayout(self._build_web_output_row(name, url))
        else:
            web_layout.addWidget(QLabel("⚠ Server unavailable"))

        left_layout.addWidget(web_group)
        left_layout.addStretch()

        # ---- Right side (transcript + bible) ---------------------------
        right_splitter = QSplitter(Qt.Vertical)
        right_splitter.setChildrenCollapsible(False)

        transcript_frame = QGroupBox("Live Transcript")
        transcript_layout = QVBoxLayout(transcript_frame)
        self._transcript_view = QTextEdit()
        self._transcript_view.setReadOnly(True)
        self._transcript_view.setFont(QFont("Georgia", 13))
        self._transcript_view.setMinimumHeight(120)
        transcript_layout.addWidget(self._transcript_view)
        right_splitter.addWidget(transcript_frame)

        bible_frame = QGroupBox("Scripture")
        bible_layout = QHBoxLayout(bible_frame)
        bible_layout.setSpacing(14)

        # ---- Left column: lookup + session history ---------------------
        history_column = QVBoxLayout()
        history_column.setSpacing(6)

        lookup_row = QHBoxLayout()
        self._ref_input = QLineEdit()
        self._ref_input.setPlaceholderText("John 3:16 or Romans 8:1-4")
        self._ref_input.returnPressed.connect(self._on_manual_ref_display)
        lookup_row.addWidget(self._ref_input)
        display_btn = QPushButton("Display")
        display_btn.clicked.connect(self._on_manual_ref_display)
        lookup_row.addWidget(display_btn)
        history_column.addLayout(lookup_row)

        self._ref_error_label = QLabel("")
        self._ref_error_label.setStyleSheet("color: #ff8a80; font-size: 11px;")
        self._ref_error_label.hide()
        history_column.addWidget(self._ref_error_label)

        list_header = QHBoxLayout()
        list_header.addWidget(_muted_label("Detected this session"))
        list_header.addStretch()
        clear_bible_btn = QPushButton("Clear")
        clear_bible_btn.setObjectName("smallBtn")
        clear_bible_btn.clicked.connect(self._clear_bible_ref)
        list_header.addWidget(clear_bible_btn)
        history_column.addLayout(list_header)

        self._bible_list = QListWidget()
        self._bible_list.setMinimumHeight(70)
        self._bible_list.itemClicked.connect(self._on_bible_item_clicked)
        history_column.addWidget(self._bible_list, stretch=1)

        # ---- Right column: current reference ---------------------------
        current_column = QVBoxLayout()
        current_column.setSpacing(6)

        self._bible_ref_label = QLabel(_NO_REFERENCE_TEXT)
        ref_font = QFont()
        ref_font.setPointSize(13)
        ref_font.setBold(True)
        self._bible_ref_label.setFont(ref_font)
        self._bible_ref_label.setStyleSheet(_REF_LABEL_EMPTY_STYLE)
        current_column.addWidget(self._bible_ref_label)

        # Verse-pair navigator — shown only for ranged references
        self._chunk_label = _muted_label("Verse pairs (click to display)")
        self._chunk_list = QListWidget()
        self._chunk_list.setObjectName("chunkList")
        self._chunk_list.setFixedHeight(64)
        self._chunk_list.setFlow(QListWidget.Flow.LeftToRight)
        self._chunk_list.setWrapping(True)
        self._chunk_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._chunk_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self._chunk_list.itemClicked.connect(self._on_chunk_clicked)
        self._chunk_label.hide()
        self._chunk_list.hide()
        current_column.addWidget(self._chunk_label)
        current_column.addWidget(self._chunk_list)

        self._bible_verse_view = QTextEdit()
        self._bible_verse_view.setReadOnly(True)
        self._bible_verse_view.setFont(QFont("Georgia", 12))
        self._bible_verse_view.setMinimumHeight(60)
        current_column.addWidget(self._bible_verse_view, stretch=1)

        bible_layout.addLayout(history_column, stretch=2)
        bible_layout.addLayout(current_column, stretch=3)
        right_splitter.addWidget(bible_frame)

        right_splitter.setStretchFactor(0, 1)
        right_splitter.setStretchFactor(1, 0)
        right_splitter.setSizes([480, 260])

        # ---- Warning / error banners -----------------------------------
        self._api_warning = _banner_label()
        self._error_banner = _banner_label()

        right_wrapper = QWidget()
        rw_layout = QVBoxLayout(right_wrapper)
        rw_layout.setContentsMargins(0, 0, 0, 0)
        rw_layout.addWidget(self._api_warning)
        rw_layout.addWidget(self._error_banner)
        rw_layout.addWidget(right_splitter)

        # ---- Assemble --------------------------------------------------
        root.addWidget(left)
        root.addWidget(right_wrapper, stretch=1)

    def _build_web_output_row(self, name: str, url: str) -> QHBoxLayout:
        """One Web Output row: name, plus Copy and Open actions for its URL."""
        row = QHBoxLayout()
        row.setSpacing(4)
        label = QLabel(name)
        label.setToolTip(url)
        row.addWidget(label)
        row.addStretch()

        copy_btn = QPushButton("Copy")
        copy_btn.setObjectName("smallBtn")
        copy_btn.setToolTip(f"Copy {url}")
        copy_btn.clicked.connect(lambda: self._copy_to_clipboard(url, copy_btn))
        row.addWidget(copy_btn)

        open_btn = QPushButton("Open")
        open_btn.setObjectName("smallBtn")
        open_btn.setToolTip(f"Open {url}")
        open_btn.clicked.connect(lambda: webbrowser.open(url))
        row.addWidget(open_btn)
        return row

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
        self._status_badge.setText(f"● {status}  ·  {provider}")
        self._status_badge.setStyleSheet(f"color: {color}; font-weight: bold;")
        if status in ("Connecting", "Live"):
            self._error_banner.setVisible(False)
        if self._web_server:
            self._web_server.broadcast_status(status)

    # ------------------------------------------------------------------
    # Transcript display
    # ------------------------------------------------------------------

    def _rebuild_transcript(self) -> None:
        """Repaint the transcript view from the manager's current state."""
        scrollbar = self._transcript_view.verticalScrollBar()
        previous_position = scrollbar.value()
        # Only follow new text if the operator hasn't scrolled back to read.
        follow_tail = previous_position >= scrollbar.maximum() - 4

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

        scrollbar.setValue(scrollbar.maximum() if follow_tail else previous_position)

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
        self._push_state()

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
        """Pause button click — toggles based on current state."""
        if not self._paused:
            self._pause_transcription()
        else:
            self._resume_transcription()

    @Slot()
    def _pause_transcription(self) -> None:
        """
        Application-level pause operation. Shared by the Pause button and
        the web control panel (POST /api/transcription/pause) — uses the
        engine's existing pause semantics; the engine is never stopped.
        """
        if not self._session_active or self._paused:
            return
        self._paused = True
        self._sig_pause.emit()
        self._pause_btn.setText("Resume")
        self._set_status("Paused")
        self._push_state()

    @Slot()
    def _resume_transcription(self) -> None:
        """Application-level resume operation. See _pause_transcription()."""
        if not self._session_active or not self._paused:
            return
        self._paused = False
        self._sig_resume.emit()
        self._pause_btn.setText("Pause")
        self._set_status("Live")
        self._push_state()

    @Slot()
    def _on_stop(self) -> None:
        self._session_active = False
        self._stop_worker()

        self._start_btn.setEnabled(True)
        self._pause_btn.setEnabled(False)
        self._pause_btn.setText("Pause")
        self._stop_btn.setEnabled(False)
        self._device_combo.setEnabled(True)
        self._refresh_btn.setEnabled(True)
        self._engine_combo.setEnabled(True)
        self._level_bar.setValue(0)

        has_content = bool(self._manager.get_segments())
        self._save_btn.setEnabled(has_content)
        self._set_status("Disconnected")
        self._push_state()

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
        self._error_banner.setText(f"⚠  {message}")
        self._error_banner.setVisible(True)

    @Slot(float)
    def _on_level(self, level: float) -> None:
        value = _level_to_meter(level)
        self._level_bar.setValue(value)
        color = next(c for floor, c in _LEVEL_BANDS if value >= floor)
        if color != self._level_color:
            self._level_color = color
            self._level_bar.setStyleSheet(
                f"QProgressBar::chunk {{ background: {color}; border-radius: 3px; }}"
            )

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
        self._push_state()

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
            self._ref_error_label.show()
            return
        self._ref_error_label.hide()
        self._display_manual_reference(refs[0])

    @Slot(str)
    def _manual_ref_from_web(self, text: str) -> None:
        """
        Application-level operation: parse and display an operator-entered
        reference. Shared by the desktop manual-lookup field
        (_on_manual_ref_display) and the web control panel
        (POST /api/bible/manual, which already validated that `text`
        parses before invoking this).
        """
        refs = bible_detector.detect_all(text.strip())
        if not refs:
            return
        self._display_manual_reference(refs[0])

    def _display_manual_reference(self, ref: BibleReference) -> None:
        # Add to session history so it appears in the detected list
        self._bible_history.add_or_upgrade(ref)
        self._show_bible_ref(ref, auto_select=True)

    def _show_bible_ref(self, ref: BibleReference, *, auto_select: bool = True) -> None:
        self._selected_ref = ref
        self._bible_ref_label.setText(ref.display())
        self._bible_ref_label.setStyleSheet(_REF_LABEL_ACTIVE_STYLE)
        self._populate_chunk_list(ref)
        chunks = self._get_chunks()
        if chunks:
            # Long passages: start on the first 2-verse pair; the rest are
            # listed in the verse-pair navigator for the operator to click
            # through as the reader reads.
            self._current_chunk_index = 0
            self._chunk_list.setCurrentRow(0)
            verse_text = self._get_verse_range_text(chunks[0])
        else:
            self._current_chunk_index = None
            verse_text = self._get_verse_range_text(ref)
        self._bible_verse_view.setPlainText(verse_text)
        if auto_select:
            self._refresh_bible_list()
        self._broadcast_current_bible()
        self._push_state()

    @Slot(str)
    def _select_reference_by_key(self, key: str) -> None:
        """
        Application-level operation: select a reference already present in
        the session's ReferenceHistory. Shared by the history list click
        (PySide6) and the web control panel (POST /api/bible/select).

        Only allows references that exist in ReferenceHistory — an unknown
        key is silently ignored (the web layer validates before calling
        this, but this method stays defensive since it is also reachable
        directly).
        """
        ref = next(
            (r for r in self._bible_history.get_all() if r.normalized_key() == key),
            None,
        )
        if ref is None:
            return
        self._show_bible_ref(ref, auto_select=True)

    @Slot(bool)
    def _set_bible_visible(self, visible: bool) -> None:
        """
        Show/hide the Bible passage on the web output without clearing the
        current reference. Shared by the web control panel
        (POST /api/bible/visibility).
        """
        visible = bool(visible)
        if visible == self._bible_visible:
            return
        self._bible_visible = visible
        self._broadcast_current_bible()
        self._push_state()

    @Slot(str)
    def _set_display_mode(self, mode: str) -> None:
        """
        Switch the web output between 'subtitles_bible' and 'bible_only'.
        Purely a display setting — never touches transcription or Bible
        detection. Shared by the web control panel (POST /api/display-mode).
        """
        if mode not in _VALID_DISPLAY_MODES or mode == self._display_mode:
            return
        self._display_mode = mode
        self._push_state()

    @Slot(str)
    def _set_bible_style(self, style: str) -> None:
        """
        Switch the web output's Bible verse styling to one of BIBLE_STYLES.
        Purely a display/appearance setting — never touches transcription,
        Bible detection, or the current reference.
        Shared by the web control panel (POST /api/bible/style).
        """
        if style not in BIBLE_STYLES or style == self._bible_style:
            return
        self._bible_style = style
        self._push_state()

    def _broadcast_current_bible(self) -> None:
        """
        Push the currently selected Bible reference/text to the web output,
        respecting the bible_visible flag. Hiding sends an empty payload to
        the output page without touching self._selected_ref, so re-showing
        immediately restores the same passage.

        For long (ranged) references, this reflects whichever verse-pair
        chunk is currently selected (defaulting to the first pair) rather
        than the entire range at once — matching what is shown locally.
        """
        if not self._web_server:
            return
        if self._bible_visible and self._selected_ref:
            ref_display, verse_text = self._current_display_text()
            self._web_server.broadcast_bible(ref_display, verse_text)
        else:
            self._web_server.broadcast_bible("", "")

    def _current_display_text(self) -> tuple[str, str]:
        """
        Return the (reference display, verse text) currently shown for
        self._selected_ref — the active verse-pair chunk if one is
        selected, otherwise the full reference.
        """
        chunks = self._get_chunks()
        if (
            chunks
            and self._current_chunk_index is not None
            and 0 <= self._current_chunk_index < len(chunks)
        ):
            chunk = chunks[self._current_chunk_index]
            return chunk.display(), self._get_verse_range_text(chunk)
        return self._selected_ref.display(), self._get_verse_range_text(self._selected_ref)

    def _build_state_snapshot(self) -> dict:
        """
        Assemble the canonical application-state snapshot broadcast to every
        connected web client (output page + control panel) and cached by
        the web server for command validation.
        """
        history = self._bible_history.get_all()
        chunks = self._get_chunks()
        current_display, current_text = (
            self._current_display_text() if self._selected_ref else (None, None)
        )
        return {
            "type": "state",
            "transcription": {
                "running": self._session_active,
                "paused": self._paused,
            },
            "bible": {
                "current_reference": (
                    self._selected_ref.display() if self._selected_ref else None
                ),
                "current_reference_key": (
                    self._selected_ref.normalized_key()
                    if self._selected_ref
                    else None
                ),
                "current_display": current_display,
                "current_text": current_text,
                "visible": self._bible_visible,
                "verse_chunks": [c.display() for c in chunks],
                "current_chunk_index": self._current_chunk_index,
            },
            "display": {"mode": self._display_mode, "bible_style": self._bible_style},
            "reference_history": [
                {"key": r.normalized_key(), "display": r.display()} for r in history
            ],
        }

    def _push_state(self) -> None:
        """Broadcast the current canonical state snapshot to web clients."""
        if self._web_server:
            self._web_server.push_state(self._build_state_snapshot())

    def _get_chunks(self) -> list[BibleReference]:
        """
        2-verse chunks for the current reference, or [] if not ranged/none.

        A whole-chapter reference (e.g. "Psalm 23", no verse given) is
        chunked across every verse in the chapter, same as an explicit
        range — so long chapters get the same pair-by-pair navigation.
        """
        ref = self._selected_ref
        if ref is None:
            return []
        if ref.verse_start is None:
            chapter_verses = bible_detector.lookup_chapter_verses(
                ref.book, ref.chapter
            )
            if not chapter_verses:
                return []
            ref = replace(
                ref, verse_start=1, verse_end=max(chapter_verses.keys())
            )
        elif ref.verse_end is None:
            return []
        chunks = self._split_into_pairs(ref)
        return chunks if len(chunks) > 1 else []

    def _populate_chunk_list(self, ref: BibleReference) -> None:
        """Populate the verse-pair navigator when ref spans multiple verses."""
        self._chunk_list.clear()
        chunks = self._get_chunks()
        if not chunks:
            self._chunk_label.hide()
            self._chunk_list.hide()
            return

        self._chunk_label.show()
        self._chunk_list.show()
        for chunk in chunks:
            verses = f"{chunk.verse_start}"
            if chunk.verse_end is not None:
                verses += f"–{chunk.verse_end}"
            item = QListWidgetItem(verses)
            item.setToolTip(chunk.display())
            item.setData(Qt.ItemDataRole.UserRole, chunk)
            self._chunk_list.addItem(item)

    @staticmethod
    def _split_into_pairs(ref: BibleReference) -> list[BibleReference]:
        """Split a ranged reference into 2-verse chunks."""
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
        index = self._chunk_list.row(item)
        self._select_chunk_by_index(index)

    @Slot(int)
    def _select_chunk_by_index(self, index: int) -> None:
        """
        Application-level operation: display one 2-verse chunk of the
        current ranged reference. Shared by the desktop verse-pair
        navigator and the web control panel
        (POST /api/bible/chunk). Does not change the current reference
        or the reference history — only the displayed verse text.
        """
        chunks = self._get_chunks()
        if index < 0 or index >= len(chunks):
            return
        chunk = chunks[index]
        self._current_chunk_index = index
        self._chunk_list.setCurrentRow(index)
        self._bible_verse_view.setPlainText(self._get_verse_range_text(chunk))
        self._broadcast_current_bible()
        self._push_state()

    def _get_verse_range_text(self, ref: BibleReference) -> str:
        if ref.verse_start is None:
            # Book + chapter only (e.g. "Psalm 23") — load every verse in
            # the chapter rather than showing nothing.
            chapter_verses = bible_detector.lookup_chapter_verses(
                ref.book, ref.chapter
            )
            if not chapter_verses:
                return ""
            return "\n".join(
                f"{v}  {text}" for v, text in sorted(chapter_verses.items())
            )
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
        self._bible_history.clear()
        self._selected_ref = None
        self._current_chunk_index = None
        self._bible_list.clear()
        self._bible_ref_label.setText(_NO_REFERENCE_TEXT)
        self._bible_ref_label.setStyleSheet(_REF_LABEL_EMPTY_STYLE)
        self._bible_verse_view.clear()
        self._chunk_list.clear()
        self._chunk_label.hide()
        self._chunk_list.hide()
        self._broadcast_current_bible()
        self._push_state()

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


def _muted_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("muted")
    return label


def _banner_label() -> QLabel:
    label = QLabel()
    label.setObjectName("banner")
    label.setWordWrap(True)
    label.setVisible(False)
    return label


def _level_to_meter(level: float) -> int:
    """
    Map a linear audio level (0.0-1.0) to a 0-100 meter position on a
    -60 dBFS..0 dBFS scale. Speech sits around 0.01-0.1 linear, which would
    barely move a linear meter.
    """
    if level <= 0:
        return 0
    decibels = 20 * math.log10(level)
    return max(0, min(100, round((decibels + 60) / 60 * 100)))
