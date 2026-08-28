"""
Tests for the web output server.

All tests are synchronous.  The server is started on an ephemeral port
(18765) so it does not conflict with a running application instance.

WebSocket tests use the websockets.sync.client which ships with
websockets >= 11 (already a transitive dependency of deepgram-sdk).
"""

import json
import sys
import os
import time
import threading
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import websockets.sync.client as ws_sync

from web.server import WebOutputServer
from transcript.models import TranscriptSegment, BibleReference


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TEST_PORT = 18765


@pytest.fixture(scope="module")
def server():
    """Start one WebOutputServer for the whole test module, stop it after."""
    srv = WebOutputServer(host="localhost", port=TEST_PORT)
    srv.start()
    yield srv
    srv.stop()


def _ws_connect():
    return ws_sync.connect(f"ws://localhost:{TEST_PORT}/ws")


def _recv_json(ws):
    return json.loads(ws.recv(timeout=5))


def _final_seg(text="Hello world.", start=0.0, end=1.0):
    return TranscriptSegment(start=start, end=end, text=text, final=True)


def _interim_seg(text="Hello wor"):
    return TranscriptSegment(start=0.0, end=0.5, text=text, final=False)


# ---------------------------------------------------------------------------
# 1. HTTP server starts
# ---------------------------------------------------------------------------

class TestHttpServer:
    def test_output_page_returns_200(self, server):
        resp = urllib.request.urlopen(f"http://localhost:{TEST_PORT}/output")
        assert resp.status == 200

    def test_root_redirects_to_output(self, server):
        resp = urllib.request.urlopen(
            f"http://localhost:{TEST_PORT}/", timeout=5
        )
        # urlopen follows redirects; final URL should end with /output
        assert resp.url.endswith("/output")

    def test_static_css_served(self, server):
        resp = urllib.request.urlopen(
            f"http://localhost:{TEST_PORT}/static/style.css", timeout=5
        )
        assert resp.status == 200

    def test_static_js_served(self, server):
        resp = urllib.request.urlopen(
            f"http://localhost:{TEST_PORT}/static/app.js", timeout=5
        )
        assert resp.status == 200


# ---------------------------------------------------------------------------
# 2. WebSocket connection + init message
# ---------------------------------------------------------------------------

class TestWebSocketConnect:
    def test_ws_connects_and_receives_init(self, server):
        with _ws_connect() as ws:
            msg = _recv_json(ws)
            assert msg["type"] == "init"
            assert "segments" in msg
            assert "interim" in msg
            assert "status" in msg

    def test_init_has_empty_state_on_fresh_server(self, server):
        # Use a fresh server instance so there is no prior state
        fresh = WebOutputServer(host="localhost", port=TEST_PORT + 1)
        fresh.start()
        try:
            with ws_sync.connect(f"ws://localhost:{TEST_PORT + 1}/ws") as ws:
                msg = _recv_json(ws)
                assert msg["segments"] == []
                assert msg["interim"] == ""
                assert msg["bible"] is None
        finally:
            fresh.stop()


# ---------------------------------------------------------------------------
# 3. Interim transcript update
# ---------------------------------------------------------------------------

class TestInterimTranscript:
    def test_interim_broadcast_received(self, server):
        with _ws_connect() as ws:
            _recv_json(ws)  # discard init
            server.broadcast_transcript(_interim_seg("Let's turn to"))
            msg = _recv_json(ws)
            assert msg["type"] == "transcript"
            assert msg["final"] is False
            assert msg["text"] == "Let's turn to"
            assert "start" not in msg  # interim carries no timestamps
            assert "end" not in msg

    def test_interim_updates_server_state(self, server):
        server.broadcast_transcript(_interim_seg("Romans chapter"))
        # A new client joining now should see the interim in init
        with _ws_connect() as ws:
            msg = _recv_json(ws)
            assert msg["interim"] == "Romans chapter"


# ---------------------------------------------------------------------------
# 4. Final transcript segments
# ---------------------------------------------------------------------------

class TestFinalTranscript:
    def test_final_broadcast_received(self, server):
        with _ws_connect() as ws:
            _recv_json(ws)  # discard init
            server.broadcast_transcript(_final_seg("Welcome everyone.", 0.0, 2.5))
            msg = _recv_json(ws)
            assert msg["type"] == "transcript"
            assert msg["final"] is True
            assert msg["text"] == "Welcome everyone."
            assert msg["start"] == pytest.approx(0.0)
            assert msg["end"] == pytest.approx(2.5)

    def test_final_clears_interim_in_state(self, server):
        server.broadcast_transcript(_interim_seg("some interim"))
        server.broadcast_transcript(_final_seg("Finalized text."))
        # New client should see no interim
        with _ws_connect() as ws:
            msg = _recv_json(ws)
            assert msg["interim"] == ""
            assert any(s["text"] == "Finalized text." for s in msg["segments"])


# ---------------------------------------------------------------------------
# 5. Bible reference
# ---------------------------------------------------------------------------

class TestBibleReference:
    def test_bible_broadcast_received(self, server):
        with _ws_connect() as ws:
            _recv_json(ws)  # discard init
            server.broadcast_bible("Romans 8:1",
                                   "There is therefore now no condemnation...")
            msg = _recv_json(ws)
            assert msg["type"] == "bible_reference"
            assert msg["reference"] == "Romans 8:1"
            assert "no condemnation" in msg["text"]

    def test_bible_in_init_after_detection(self, server):
        server.broadcast_bible("John 3:16", "For God so loved the world...")
        with _ws_connect() as ws:
            msg = _recv_json(ws)
            assert msg["bible"] is not None
            assert msg["bible"]["reference"] == "John 3:16"


# ---------------------------------------------------------------------------
# 6. New client receives current state (late join)
# ---------------------------------------------------------------------------

class TestLateJoin:
    def test_late_join_receives_existing_segments(self, server):
        # Use a fresh server to have a clean segment list
        fresh = WebOutputServer(host="localhost", port=TEST_PORT + 2)
        fresh.start()
        try:
            fresh.broadcast_transcript(_final_seg("First sentence."))
            fresh.broadcast_transcript(_final_seg("Second sentence."))
            time.sleep(0.05)  # let broadcasts propagate
            with ws_sync.connect(f"ws://localhost:{TEST_PORT + 2}/ws") as ws:
                msg = _recv_json(ws)
                texts = [s["text"] for s in msg["segments"]]
                assert "First sentence." in texts
                assert "Second sentence." in texts
        finally:
            fresh.stop()


# ---------------------------------------------------------------------------
# 7. Multiple clients receive the same events
# ---------------------------------------------------------------------------

class TestMultipleClients:
    def test_two_clients_both_receive(self, server):
        received = [[], []]

        def client(idx):
            with _ws_connect() as ws:
                _recv_json(ws)  # discard init
                msg = _recv_json(ws)
                received[idx].append(msg)

        t1 = threading.Thread(target=client, args=(0,))
        t2 = threading.Thread(target=client, args=(1,))
        t1.start()
        t2.start()
        time.sleep(0.1)  # let both threads connect
        server.broadcast_transcript(_final_seg("Broadcast test."))
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert len(received[0]) == 1
        assert len(received[1]) == 1
        assert received[0][0]["text"] == "Broadcast test."
        assert received[1][0]["text"] == "Broadcast test."


# ---------------------------------------------------------------------------
# 8. Reconnect — after disconnect, client gets current state on reconnect
# ---------------------------------------------------------------------------

class TestReconnect:
    def test_reconnect_receives_updated_state(self, server):
        fresh = WebOutputServer(host="localhost", port=TEST_PORT + 3)
        fresh.start()
        try:
            # First connection — see empty state
            with ws_sync.connect(f"ws://localhost:{TEST_PORT + 3}/ws") as ws:
                msg = _recv_json(ws)
                assert msg["segments"] == []
            # While disconnected, server gets new segments
            fresh.broadcast_transcript(_final_seg("After reconnect."))
            time.sleep(0.05)
            # Reconnect — should get the new segment
            with ws_sync.connect(f"ws://localhost:{TEST_PORT + 3}/ws") as ws:
                msg = _recv_json(ws)
                assert any(s["text"] == "After reconnect." for s in msg["segments"])
        finally:
            fresh.stop()
