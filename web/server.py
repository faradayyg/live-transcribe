"""
Local HTTP + WebSocket server for the web transcript output.

Runs in a background daemon thread with its own asyncio event loop so it
never blocks the Qt GUI.  Thread-safe broadcast methods let the Qt main
thread push updates to all connected browser clients.

Endpoints
---------
GET  /           → 302 redirect to /output
GET  /output     → the transcript output page (HTML)
GET  /ws         → WebSocket upgrade

WebSocket protocol (server → client, all JSON)
-----------------------------------------------
On connect:
  {"type": "init",
   "segments": [{"text": "...", "start": 0.0, "end": 0.0}, ...],
   "interim": "...",
   "bible": {"reference": "...", "text": "..."} | null,
   "status": "disconnected"}

Live updates:
  {"type": "transcript", "final": true,  "text": "...", "start": 0.0, "end": 0.0}
  {"type": "transcript", "final": false, "text": "..."}
  {"type": "bible_reference", "reference": "Romans 8:1",
   "text": "There is therefore now no condemnation..."}
  {"type": "status", "status": "live"}

No messages are expected from the client; browsers are receive-only.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from pathlib import Path
from typing import Optional

from aiohttp import web

log = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).parent / "static"


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
        self._bible_history_list: list[dict] = []
        self._status: str = "disconnected"        # current connection status

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
            if entry and entry not in self._bible_history_list:
                self._bible_history_list.append(entry)
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
        app.router.add_get("/ws",     self._handle_ws)
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
                    "bible_history": list(self._bible_history_list),
                    "status": self._status,
                }
            await ws.send_json(init)

            # Clients are output-only; drain any incoming frames and ignore them
            async for _ in ws:
                pass
        finally:
            self._clients.discard(ws)
            log.debug("WebSocket client disconnected (%d total)", len(self._clients))

        return ws

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
