"""
Local HTTP + WebSocket server for the web transcript output and the
operator control panel.

Runs in a background daemon thread with its own asyncio event loop so it
never blocks the Qt GUI.  Thread-safe broadcast methods let the Qt main
thread push updates to all connected browser clients.

Endpoints
---------
GET  /                          → 302 redirect to /output
GET  /output                    → the transcript output page (HTML)
GET  /control                   → the operator control panel (HTML)
GET  /ws                        → WebSocket upgrade (shared by both pages)
POST /api/transcription/pause   → pause transcription (existing pause semantics)
POST /api/transcription/resume  → resume transcription
POST /api/bible/select          → {"key": "<normalized_key>"} select a history entry
POST /api/bible/visibility      → {"visible": true|false} show/hide Bible on output
POST /api/display-mode          → {"mode": "subtitles_bible"|"bible_only"}

WebSocket protocol (server → client, all JSON)
-----------------------------------------------
On connect:
  {"type": "init",
   "segments": [{"text": "...", "start": 0.0, "end": 0.0}, ...],
   "interim": "...",
   "bible": {"reference": "...", "text": "..."} | null,
   "status": "disconnected"}
  followed immediately by the last known canonical state (if any), e.g.:
  {"type": "state",
   "transcription": {"running": true, "paused": false},
   "bible": {"current_reference": "Romans 8:1-4",
             "current_reference_key": "romans:8:1:4", "visible": true},
   "display": {"mode": "subtitles_bible"},
   "reference_history": [{"key": "...", "display": "Romans 8:1-4"}, ...]}

Live updates:
  {"type": "transcript", "final": true,  "text": "...", "start": 0.0, "end": 0.0}
  {"type": "transcript", "final": false, "text": "..."}
  {"type": "bible_reference", "reference": "Romans 8:1",
   "text": "There is therefore now no condemnation..."}
  {"type": "status", "status": "live"}
  {"type": "state", ...}   — canonical control state, see above; broadcast
                             after every successful control mutation

The output page is receive-only over the WebSocket. The control panel issues
commands via the POST /api/... endpoints above; results are observed by
every connected client (including the sender) through the "state" broadcast,
never by mutating local browser state directly.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from pathlib import Path
from typing import Callable, Optional

from aiohttp import web

log = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).parent / "static"
_VALID_DISPLAY_MODES = ("subtitles_bible", "bible_only")


class WebOutputServer:
    """
    Lightweight aiohttp HTTP + WebSocket server.

    Lifecycle::

        server = WebOutputServer(host="localhost", port=8765)
        server.start()          # returns immediately; server runs in background
        ...
        server.stop()           # blocks until the server thread has exited
    """

    DEFAULT_HOST = "localhost"
    DEFAULT_PORT = 8765

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
        self._host = host
        self._port = port

        # asyncio primitives — created inside the server thread
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._runner: Optional[web.AppRunner] = None

        # Connected WebSocket clients (only touched inside the asyncio loop)
        self._clients: set[web.WebSocketResponse] = set()

        # Current state — written from Qt thread, read from asyncio thread
        self._state_lock = threading.Lock()
        self._segments: list[dict] = []          # finalized segments
        self._interim: str = ""                   # current interim text
        self._bible: Optional[dict] = None        # last Bible reference
        self._status: str = "disconnected"        # current connection status

        # Canonical control-state snapshot (see MainWindow._build_state_snapshot).
        # Populated the first time push_state() is called; used both to seed
        # late-joining clients and to validate incoming control commands.
        self._last_state: dict = {}

        # Control command callbacks — registered by MainWindow via
        # set_control_callbacks(). Each callback is expected to be safe to
        # call from this server's background thread (e.g. a Qt signal's
        # thread-safe .emit bound method).
        self._pause_cb: Optional[Callable[[], None]] = None
        self._resume_cb: Optional[Callable[[], None]] = None
        self._select_reference_cb: Optional[Callable[[str], None]] = None
        self._set_bible_visible_cb: Optional[Callable[[bool], None]] = None
        self._set_display_mode_cb: Optional[Callable[[str], None]] = None
        self._select_chunk_cb: Optional[Callable[[int], None]] = None

        # Background thread
        self._thread: Optional[threading.Thread] = None
        self._started = threading.Event()         # set once the server is listening

    # ------------------------------------------------------------------
    # Public API (called from Qt thread)
    # ------------------------------------------------------------------

    @property
    def url(self) -> str:
        scheme = "http"
        host = self._host if self._host != "0.0.0.0" else "localhost"
        return f"{scheme}://{host}:{self._port}/output"

    @property
    def control_url(self) -> str:
        scheme = "http"
        host = self._host if self._host != "0.0.0.0" else "localhost"
        return f"{scheme}://{host}:{self._port}/control"

    def set_control_callbacks(
        self,
        *,
        pause: Callable[[], None],
        resume: Callable[[], None],
        select_reference: Callable[[str], None],
        set_bible_visible: Callable[[bool], None],
        set_display_mode: Callable[[str], None],
        select_chunk: Callable[[int], None],
    ) -> None:
        """
        Register the application-level operations invoked by /control.

        Each callback must be safe to call from this server's background
        thread — MainWindow satisfies this by passing bound Qt Signal
        .emit methods, which Qt automatically queues onto the main thread.
        """
        self._pause_cb = pause
        self._resume_cb = resume
        self._select_reference_cb = select_reference
        self._set_bible_visible_cb = set_bible_visible
        self._set_display_mode_cb = set_display_mode
        self._select_chunk_cb = select_chunk

    def push_state(self, state: dict) -> None:
        """
        Called from the Qt thread whenever the canonical application state
        changes. Caches the snapshot (for late joiners + command validation)
        and broadcasts it to every connected client.
        """
        with self._state_lock:
            self._last_state = dict(state)
        self._schedule(self._broadcast(state))

    def start(self) -> None:
        """Start the server in a background daemon thread."""
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="web-output-server"
        )
        self._thread.start()
        self._started.wait(timeout=5)  # wait until the server is actually listening

    def stop(self) -> None:
        """Stop the server cleanly and wait for the background thread to exit."""
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._thread = None

    def broadcast_transcript(self, segment) -> None:
        """
        Called from the Qt thread when a transcript segment arrives.
        Updates internal state and pushes to all connected browsers.
        """
        with self._state_lock:
            if segment.final:
                self._segments.append({
                    "text": segment.text,
                    "start": segment.start,
                    "end": segment.end,
                })
                self._interim = ""
            else:
                self._interim = segment.text

        msg: dict = {"type": "transcript", "final": segment.final, "text": segment.text}
        if segment.final:
            msg["start"] = segment.start
            msg["end"] = segment.end
        self._schedule(self._broadcast(msg))

    def broadcast_bible(self, reference: str, text: str) -> None:
        """Called from the Qt thread when a Bible reference is detected or cleared."""
        entry = {"reference": reference, "text": text} if reference else None
        with self._state_lock:
            self._bible = entry
        msg = {"type": "bible_reference", "reference": reference, "text": text}
        self._schedule(self._broadcast(msg))

    def broadcast_status(self, status: str) -> None:
        """Called from the Qt thread when the transcription status changes."""
        normalised = status.lower()
        with self._state_lock:
            self._status = normalised
        self._schedule(self._broadcast({"type": "status", "status": normalised}))

    # ------------------------------------------------------------------
    # Server thread entry point
    # ------------------------------------------------------------------

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._serve())
        except Exception as exc:
            log.error("Web server error: %s", exc)
        finally:
            self._loop.close()

    # ------------------------------------------------------------------
    # aiohttp application (runs inside the server thread's event loop)
    # ------------------------------------------------------------------

    async def _serve(self) -> None:
        app = web.Application()
        app.router.add_get("/",       self._handle_root)
        app.router.add_get("/output", self._handle_output)
        app.router.add_get("/control", self._handle_control)
        app.router.add_get("/ws",     self._handle_ws)
        app.router.add_post("/api/transcription/pause",  self._handle_pause)
        app.router.add_post("/api/transcription/resume", self._handle_resume)
        app.router.add_post("/api/bible/select",         self._handle_select_reference)
        app.router.add_post("/api/bible/visibility",     self._handle_bible_visibility)
        app.router.add_post("/api/display-mode",         self._handle_display_mode)
        app.router.add_post("/api/bible/chunk",          self._handle_select_chunk)
        app.router.add_static("/static", _STATIC_DIR)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._host, self._port)
        await site.start()
        log.info("Web output server listening on %s", self.url)

        self._started.set()         # unblock WebOutputServer.start()
        self._stop_event = asyncio.Event()
        await self._stop_event.wait()

        # Cleanup
        await asyncio.gather(
            *(ws.close() for ws in list(self._clients)),
            return_exceptions=True,
        )
        await self._runner.cleanup()

    # ------------------------------------------------------------------
    # Request handlers
    # ------------------------------------------------------------------

    async def _handle_root(self, request: web.Request) -> web.Response:
        raise web.HTTPFound("/output")

    async def _handle_output(self, request: web.Request) -> web.FileResponse:
        return web.FileResponse(_STATIC_DIR / "output.html")

    async def _handle_control(self, request: web.Request) -> web.FileResponse:
        return web.FileResponse(_STATIC_DIR / "control.html")

    async def _handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self._clients.add(ws)
        log.debug("WebSocket client connected (%d total)", len(self._clients))

        try:
            # Send current state so late-joining clients catch up
            with self._state_lock:
                init = {
                    "type": "init",
                    "segments": list(self._segments),
                    "interim": self._interim,
                    "bible": self._bible,
                    "status": self._status,
                }
                state_snapshot = dict(self._last_state) if self._last_state else None
            await ws.send_json(init)
            if state_snapshot:
                await ws.send_json(state_snapshot)

            # Clients are output-only; drain any incoming frames and ignore them
            async for _ in ws:
                pass
        finally:
            self._clients.discard(ws)
            log.debug("WebSocket client disconnected (%d total)", len(self._clients))

        return ws

    # ------------------------------------------------------------------
    # Control API handlers (POST /api/...) — operator control panel
    # ------------------------------------------------------------------

    async def _handle_pause(self, request: web.Request) -> web.Response:
        with self._state_lock:
            running = self._last_state.get("transcription", {}).get("running", False)
        if not running:
            return web.json_response(
                {"error": "No active transcription session."}, status=400
            )
        if self._pause_cb is None:
            return web.json_response({"error": "Control unavailable."}, status=503)
        self._pause_cb()
        return web.json_response({"ok": True})

    async def _handle_resume(self, request: web.Request) -> web.Response:
        with self._state_lock:
            running = self._last_state.get("transcription", {}).get("running", False)
        if not running:
            return web.json_response(
                {"error": "No active transcription session."}, status=400
            )
        if self._resume_cb is None:
            return web.json_response({"error": "Control unavailable."}, status=503)
        self._resume_cb()
        return web.json_response({"ok": True})

    async def _handle_select_reference(self, request: web.Request) -> web.Response:
        body = await self._read_json(request)
        if body is None:
            return web.json_response({"error": "Invalid JSON body."}, status=400)
        key = body.get("key")
        if not key or not isinstance(key, str):
            return web.json_response({"error": "Missing 'key'."}, status=400)
        with self._state_lock:
            valid_keys = {
                entry.get("key") for entry in self._last_state.get("reference_history", [])
            }
        if key not in valid_keys:
            return web.json_response({"error": "Unknown reference."}, status=400)
        if self._select_reference_cb is None:
            return web.json_response({"error": "Control unavailable."}, status=503)
        self._select_reference_cb(key)
        return web.json_response({"ok": True})

    async def _handle_bible_visibility(self, request: web.Request) -> web.Response:
        body = await self._read_json(request)
        if body is None:
            return web.json_response({"error": "Invalid JSON body."}, status=400)
        visible = self._parse_bool(body.get("visible"))
        if visible is None:
            return web.json_response({"error": "Invalid 'visible' value."}, status=400)
        if self._set_bible_visible_cb is None:
            return web.json_response({"error": "Control unavailable."}, status=503)
        self._set_bible_visible_cb(visible)
        return web.json_response({"ok": True})

    async def _handle_display_mode(self, request: web.Request) -> web.Response:
        body = await self._read_json(request)
        if body is None:
            return web.json_response({"error": "Invalid JSON body."}, status=400)
        mode = body.get("mode")
        if mode not in _VALID_DISPLAY_MODES:
            return web.json_response({"error": "Invalid display mode."}, status=400)
        if self._set_display_mode_cb is None:
            return web.json_response({"error": "Control unavailable."}, status=503)
        self._set_display_mode_cb(mode)
        return web.json_response({"ok": True})

    async def _handle_select_chunk(self, request: web.Request) -> web.Response:
        """
        Display one 2-verse chunk of the current (multi-verse) Bible
        reference, e.g. so an operator can tap along verse-pair by
        verse-pair as the reader reads a long passage. Does not change
        the current reference or the reference history.
        """
        body = await self._read_json(request)
        if body is None:
            return web.json_response({"error": "Invalid JSON body."}, status=400)
        index = body.get("index")
        if not isinstance(index, int) or isinstance(index, bool):
            return web.json_response({"error": "Missing/invalid 'index'."}, status=400)
        with self._state_lock:
            chunk_count = len(self._last_state.get("bible", {}).get("verse_chunks", []))
        if index < 0 or index >= chunk_count:
            return web.json_response({"error": "Unknown chunk."}, status=400)
        if self._select_chunk_cb is None:
            return web.json_response({"error": "Control unavailable."}, status=503)
        self._select_chunk_cb(index)
        return web.json_response({"ok": True})

    @staticmethod
    async def _read_json(request: web.Request) -> Optional[dict]:
        try:
            body = await request.json()
        except Exception:
            return None
        return body if isinstance(body, dict) else None

    @staticmethod
    def _parse_bool(value) -> Optional[bool]:
        """Normalise assorted truthy/falsy JSON values to a strict bool."""
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in ("true", "false"):
            return value.lower() == "true"
        if isinstance(value, int) and value in (0, 1):
            return bool(value)
        return None

    # ------------------------------------------------------------------
    # Broadcast helpers
    # ------------------------------------------------------------------

    async def _broadcast(self, message: dict) -> None:
        """Send *message* to every connected WebSocket client."""
        if not self._clients:
            return
        dead: set = set()
        payload = json.dumps(message)
        for ws in list(self._clients):
            try:
                await ws.send_str(payload)
            except Exception:
                dead.add(ws)
        self._clients -= dead

    def _schedule(self, coro) -> None:
        """Thread-safe: schedule a coroutine on the server's event loop."""
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, self._loop)

    async def _shutdown(self) -> None:
        if self._stop_event:
            self._stop_event.set()
