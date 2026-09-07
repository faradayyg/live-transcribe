"""
Tests for the web control panel (/control) and its REST control API.

These tests exercise WebOutputServer's real HTTP validation and broadcast
logic. Control callbacks are simulated with a small in-test "app" harness
that mimics MainWindow's canonical-state responsibilities (mutate state,
then call server.push_state()) without requiring Qt or a running GUI.
This keeps the control-state contract itself under test while staying
decoupled from PySide6.
"""

import json
import sys
import os
import time
import threading
import urllib.request
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import websockets.sync.client as ws_sync

from web.server import WebOutputServer


TEST_PORT = 18800


# ---------------------------------------------------------------------------
# Minimal application harness — stands in for MainWindow's canonical state
# ---------------------------------------------------------------------------


class FakeApp:
    """
    A tiny stand-in for the subset of MainWindow responsible for canonical
    control state. Mirrors the real _build_state_snapshot()/_push_state()
    contract so the server's validation and broadcast logic is exercised
    exactly as it would be in the real application.
    """

    def __init__(self, server: WebOutputServer) -> None:
        self.server = server
        self.running = False
        self.paused = False
        self.bible_visible = True
        self.display_mode = "subtitles_bible"
        self.current_ref = None  # (key, display) tuple or None
        self.history: list[tuple[str, str]] = []  # (key, display)
        self.verse_chunks: list[str] = []
        self.current_chunk_index = None

        server.set_control_callbacks(
            pause=self.pause,
            resume=self.resume,
            select_reference=self.select_reference,
            set_bible_visible=self.set_bible_visible,
            set_display_mode=self.set_display_mode,
            select_chunk=self.select_chunk,
        )

    def add_reference(self, key: str, display: str) -> None:
        self.history.insert(0, (key, display))
        self.push_state()

    def start_session(self) -> None:
        self.running = True
        self.paused = False
        self.push_state()

    def stop_session(self) -> None:
        self.running = False
        self.paused = False
        self.push_state()

    def pause(self) -> None:
        if not self.running or self.paused:
            return
        self.paused = True
        self.push_state()

    def resume(self) -> None:
        if not self.running or not self.paused:
            return
        self.paused = False
        self.push_state()

    def select_reference(self, key: str) -> None:
        match = next((e for e in self.history if e[0] == key), None)
        if match is None:
            return
        self.current_ref = match
        self.current_chunk_index = None
        self.push_state()

    def set_verse_chunks(self, chunks: list[str]) -> None:
        """Simulate a ranged reference producing verse-pair chunks."""
        self.verse_chunks = chunks
        self.current_chunk_index = None
        self.push_state()

    def select_chunk(self, index: int) -> None:
        if index < 0 or index >= len(self.verse_chunks):
            return
        self.current_chunk_index = index
        self.push_state()

    def set_bible_visible(self, visible: bool) -> None:
        self.bible_visible = bool(visible)
        self.push_state()

    def set_display_mode(self, mode: str) -> None:
        if mode not in ("subtitles_bible", "bible_only"):
            return
        self.display_mode = mode
        self.push_state()

    def push_state(self) -> None:
        self.server.push_state(self.snapshot())

    def snapshot(self) -> dict:
        return {
            "type": "state",
            "transcription": {"running": self.running, "paused": self.paused},
            "bible": {
                "current_reference": self.current_ref[1] if self.current_ref else None,
                "current_reference_key": self.current_ref[0] if self.current_ref else None,
                "visible": self.bible_visible,
                "verse_chunks": self.verse_chunks,
                "current_chunk_index": self.current_chunk_index,
            },
            "display": {"mode": self.display_mode},
            "reference_history": [
                {"key": k, "display": d} for k, d in self.history
            ],
        }


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _post(port: int, path: str, body: dict):
    """POST JSON, return (status_code, decoded_json_or_None)."""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"http://localhost:{port}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=5)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _ws_connect(port: int):
    return ws_sync.connect(f"ws://localhost:{port}/ws")


def _recv_json(ws):
    return json.loads(ws.recv(timeout=5))


def _drain_to_state(ws):
    """Read messages until a 'state' message is found (skip init/others)."""
    for _ in range(10):
        msg = _recv_json(ws)
        if msg.get("type") == "state":
            return msg
    raise AssertionError("no 'state' message received")


# ---------------------------------------------------------------------------
# Fixture: one server + harness per test, unique port to avoid collisions
# ---------------------------------------------------------------------------


_next_port = [TEST_PORT]


@pytest.fixture
def app():
    _next_port[0] += 1
    port = _next_port[0]
    server = WebOutputServer(host="localhost", port=port)
    server.start()
    harness = FakeApp(server)
    harness.port = port
    yield harness
    server.stop()


# ---------------------------------------------------------------------------
# 1. Control page is served
# ---------------------------------------------------------------------------


class TestControlPage:
    def test_control_page_returns_200(self, app):
        resp = urllib.request.urlopen(f"http://localhost:{app.port}/control")
        assert resp.status == 200

    def test_control_static_assets_served(self, app):
        for asset in ("control.css", "control.js"):
            resp = urllib.request.urlopen(
                f"http://localhost:{app.port}/static/{asset}", timeout=5
            )
            assert resp.status == 200


# ---------------------------------------------------------------------------
# 2. Pause / resume
# ---------------------------------------------------------------------------


class TestPauseResume:
    def test_pause_without_running_session_rejected(self, app):
        status, body = _post(app.port, "/api/transcription/pause", {})
        assert status == 400
        assert "error" in body
        assert app.paused is False

    def test_pause_then_resume(self, app):
        app.start_session()
        status, body = _post(app.port, "/api/transcription/pause", {})
        assert status == 200
        assert body["ok"] is True
        assert app.paused is True

        status, body = _post(app.port, "/api/transcription/resume", {})
        assert status == 200
        assert app.paused is False

    def test_pause_broadcasts_state(self, app):
        app.start_session()
        with _ws_connect(app.port) as ws:
            _drain_to_state(ws)  # state pushed by start_session()
            _post(app.port, "/api/transcription/pause", {})
            msg = _drain_to_state(ws)
            assert msg["transcription"]["paused"] is True


# ---------------------------------------------------------------------------
# 3. Reference selection
# ---------------------------------------------------------------------------


class TestReferenceSelection:
    def test_select_existing_reference(self, app):
        app.add_reference("romans:8:1:4", "Romans 8:1-4")
        status, body = _post(app.port, "/api/bible/select", {"key": "romans:8:1:4"})
        assert status == 200
        assert app.current_ref == ("romans:8:1:4", "Romans 8:1-4")

    def test_select_unknown_reference_rejected(self, app):
        app.add_reference("john:3:16:None", "John 3:16")
        status, body = _post(app.port, "/api/bible/select", {"key": "does:not:exist"})
        assert status == 400
        assert "error" in body
        assert app.current_ref is None  # unchanged

    def test_select_missing_key_rejected(self, app):
        status, body = _post(app.port, "/api/bible/select", {})
        assert status == 400

    def test_selecting_older_reference_does_not_change_history(self, app):
        app.add_reference("psalm:23:None:None", "Psalm 23")
        app.add_reference("romans:8:1:4", "Romans 8:1-4")
        before = list(app.history)
        _post(app.port, "/api/bible/select", {"key": "psalm:23:None:None"})
        assert app.history == before
        assert app.current_ref == ("psalm:23:None:None", "Psalm 23")


# ---------------------------------------------------------------------------
# 4. Hide / show Bible
# ---------------------------------------------------------------------------


class TestBibleVisibility:
    def test_hide_bible(self, app):
        app.add_reference("romans:8:1:4", "Romans 8:1-4")
        app.select_reference("romans:8:1:4")
        status, _ = _post(app.port, "/api/bible/visibility", {"visible": False})
        assert status == 200
        assert app.bible_visible is False
        # Current reference must remain selected
        assert app.current_ref == ("romans:8:1:4", "Romans 8:1-4")

    def test_show_bible_restores_without_reselecting(self, app):
        app.add_reference("romans:8:1:4", "Romans 8:1-4")
        app.select_reference("romans:8:1:4")
        app.set_bible_visible(False)
        status, _ = _post(app.port, "/api/bible/visibility", {"visible": True})
        assert status == 200
        assert app.bible_visible is True
        assert app.current_ref == ("romans:8:1:4", "Romans 8:1-4")

    def test_invalid_visibility_value_rejected(self, app):
        status, body = _post(app.port, "/api/bible/visibility", {"visible": "maybe"})
        assert status == 400


# ---------------------------------------------------------------------------
# 5. Display mode
# ---------------------------------------------------------------------------


class TestDisplayMode:
    def test_set_bible_only(self, app):
        status, _ = _post(app.port, "/api/display-mode", {"mode": "bible_only"})
        assert status == 200
        assert app.display_mode == "bible_only"

    def test_set_subtitles_bible(self, app):
        app.set_display_mode("bible_only")
        status, _ = _post(app.port, "/api/display-mode", {"mode": "subtitles_bible"})
        assert status == 200
        assert app.display_mode == "subtitles_bible"

    def test_invalid_mode_rejected(self, app):
        status, body = _post(app.port, "/api/display-mode", {"mode": "nonsense"})
        assert status == 400
        assert app.display_mode == "subtitles_bible"  # unchanged


# ---------------------------------------------------------------------------
# 5b. Verse-pair navigator (long ranged references)
# ---------------------------------------------------------------------------


class TestVersePairNavigator:
    def test_select_chunk(self, app):
        app.set_verse_chunks(["Romans 8:1-2", "Romans 8:3-4", "Romans 8:5"])
        status, body = _post(app.port, "/api/bible/chunk", {"index": 1})
        assert status == 200
        assert body["ok"] is True
        assert app.current_chunk_index == 1

    def test_select_chunk_out_of_range_rejected(self, app):
        app.set_verse_chunks(["Romans 8:1-2", "Romans 8:3-4"])
        status, body = _post(app.port, "/api/bible/chunk", {"index": 5})
        assert status == 400
        assert "error" in body
        assert app.current_chunk_index is None

    def test_select_chunk_without_any_chunks_rejected(self, app):
        status, body = _post(app.port, "/api/bible/chunk", {"index": 0})
        assert status == 400
        assert app.current_chunk_index is None

    def test_select_chunk_missing_index_rejected(self, app):
        app.set_verse_chunks(["Romans 8:1-2"])
        status, body = _post(app.port, "/api/bible/chunk", {})
        assert status == 400

    def test_select_chunk_broadcasts_state(self, app):
        app.set_verse_chunks(["Romans 8:1-2", "Romans 8:3-4"])
        with _ws_connect(app.port) as ws:
            _drain_to_state(ws)  # from set_verse_chunks
            _post(app.port, "/api/bible/chunk", {"index": 1})
            msg = _drain_to_state(ws)
            assert msg["bible"]["current_chunk_index"] == 1


# ---------------------------------------------------------------------------
# 6. State synchronization over WebSocket
# ---------------------------------------------------------------------------


class TestStateSync:
    def test_new_reference_reaches_control_panel_without_refresh(self, app):
        with _ws_connect(app.port) as ws:
            _recv_json(ws)  # init
            app.add_reference("john:3:16:None", "John 3:16")
            msg = _drain_to_state(ws)
            assert any(
                e["display"] == "John 3:16" for e in msg["reference_history"]
            )

    def test_multiple_clients_receive_same_state(self, app):
        received = [[], []]

        def client(idx):
            with _ws_connect(app.port) as ws:
                _recv_json(ws)  # init
                msg = _drain_to_state(ws)
                received[idx].append(msg)

        t1 = threading.Thread(target=client, args=(0,))
        t2 = threading.Thread(target=client, args=(1,))
        t1.start()
        t2.start()
        time.sleep(0.1)
        app.set_display_mode("bible_only")
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert received[0][0]["display"]["mode"] == "bible_only"
        assert received[1][0]["display"]["mode"] == "bible_only"

    def test_late_join_receives_current_state(self, app):
        app.add_reference("romans:8:1:4", "Romans 8:1-4")
        app.select_reference("romans:8:1:4")
        app.set_bible_visible(False)
        with _ws_connect(app.port) as ws:
            _recv_json(ws)  # init
            msg = _drain_to_state(ws)
            assert msg["bible"]["current_reference"] == "Romans 8:1-4"
            assert msg["bible"]["visible"] is False


# ---------------------------------------------------------------------------
# 7. Control unavailable (no callbacks registered)
# ---------------------------------------------------------------------------


class TestControlUnavailable:
    def test_pause_returns_503_when_no_callbacks_registered(self):
        _next_port[0] += 1
        port = _next_port[0]
        server = WebOutputServer(host="localhost", port=port)
        server.start()
        try:
            # Simulate a running session in cached state without registering
            # any control callbacks.
            server.push_state({
                "type": "state",
                "transcription": {"running": True, "paused": False},
                "bible": {"current_reference": None, "current_reference_key": None, "visible": True},
                "display": {"mode": "subtitles_bible"},
                "reference_history": [],
            })
            status, body = _post(port, "/api/transcription/pause", {})
            assert status == 503
        finally:
            server.stop()
