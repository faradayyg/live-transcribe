/**
 * Live Transcriber — operator control panel client.
 *
 * Architectural rule: this browser NEVER owns authoritative state.
 * Every control click sends a command to the application via the REST
 * API; the resulting UI update happens only when the server broadcasts
 * the updated canonical "state" message over the shared WebSocket. This
 * keeps /control, /output and PySide6 all observing the same source of
 * truth.
 */

"use strict";

const WS_PATH           = "/ws";
const RECONNECT_BASE_MS = 1_500;
const RECONNECT_MAX_MS  = 30_000;

// -----------------------------------------------------------------------
// DOM references
// -----------------------------------------------------------------------

const connDotEl        = document.getElementById("conn-dot");
const statusBadgeEl    = document.getElementById("status-badge");
const pauseBtnEl        = document.getElementById("pause-btn");
const modeSubsBtnEl     = document.getElementById("mode-subs-btn");
const modeBibleBtnEl    = document.getElementById("mode-bible-btn");
const bibleVisibleBtnEl = document.getElementById("bible-visible-btn");
const chunkSectionEl    = document.getElementById("chunk-section");
const chunkListEl       = document.getElementById("chunk-list");
const refListEl         = document.getElementById("ref-list");
const errorToastEl      = document.getElementById("error-toast");

// -----------------------------------------------------------------------
// Local (non-authoritative) mirror of the last known canonical state.
// Used only to render the UI and to know what to toggle *to* on the next
// command — never trusted in place of a fresh broadcast.
// -----------------------------------------------------------------------

let lastState  = null;
let socket     = null;
let reconnectDelay = RECONNECT_BASE_MS;
let toastTimer = null;

// -----------------------------------------------------------------------
// WebSocket
// -----------------------------------------------------------------------

function connect() {
  const url = `ws://${window.location.host}${WS_PATH}`;
  setDot("reconnecting");
  socket = new WebSocket(url);

  socket.addEventListener("open", () => {
    reconnectDelay = RECONNECT_BASE_MS;
    setDot("connected");
  });

  socket.addEventListener("message", (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    if (msg.type === "state") applyState(msg);
    // "init"/"transcript"/"bible_reference" are irrelevant to the control
    // panel — it only ever reflects canonical control state.
  });

  socket.addEventListener("close", () => {
    setDot("disconnected");
    socket = null;
    // Do NOT reset controls to defaults — keep showing the last known
    // state until a fresh one arrives after reconnecting.
    setTimeout(() => {
      connect();
      reconnectDelay = Math.min(reconnectDelay * 2, RECONNECT_MAX_MS);
    }, reconnectDelay);
  });

  socket.addEventListener("error", () => { socket && socket.close(); });
}

function setDot(state) { connDotEl.className = state; }

// -----------------------------------------------------------------------
// Render from canonical state
// -----------------------------------------------------------------------

function applyState(state) {
  lastState = state;
  render();
}

function render() {
  if (!lastState) return;
  renderTranscription(lastState.transcription);
  renderDisplayMode(lastState.display);
  renderBibleVisibility(lastState.bible);
  renderChunks(lastState.bible);
  renderReferenceHistory(lastState.reference_history, lastState.bible);
}

function renderTranscription(t) {
  const running = !!(t && t.running);
  const paused  = !!(t && t.paused);

  pauseBtnEl.disabled = !running;
  pauseBtnEl.classList.toggle("paused", paused);

  if (!running) {
    pauseBtnEl.textContent = "PAUSE";
    statusBadgeEl.textContent = "● DISCONNECTED";
    statusBadgeEl.className = "badge disconnected";
  } else if (paused) {
    pauseBtnEl.textContent = "START";
    statusBadgeEl.textContent = "⏸ PAUSED";
    statusBadgeEl.className = "badge paused";
  } else {
    pauseBtnEl.textContent = "PAUSE";
    statusBadgeEl.textContent = "● LIVE";
    statusBadgeEl.className = "badge live";
  }
}

function renderDisplayMode(display) {
  const mode = (display && display.mode) || "subtitles_bible";
  modeSubsBtnEl.classList.toggle("active", mode === "subtitles_bible");
  modeBibleBtnEl.classList.toggle("active", mode === "bible_only");
}

function renderBibleVisibility(bible) {
  const visible = !bible || bible.visible !== false;
  bibleVisibleBtnEl.textContent = visible ? "HIDE BIBLE" : "SHOW BIBLE";
  bibleVisibleBtnEl.classList.toggle("hidden-state", !visible);
}

function renderChunks(bible) {
  const chunks = (bible && bible.verse_chunks) || [];
  chunkListEl.innerHTML = "";

  if (chunks.length === 0) {
    chunkSectionEl.classList.add("hidden");
    return;
  }
  chunkSectionEl.classList.remove("hidden");

  const currentIndex = bible ? bible.current_chunk_index : null;
  chunks.forEach((display, index) => {
    const li = document.createElement("li");
    li.textContent = display;
    if (index === currentIndex) li.classList.add("current");
    li.addEventListener("click", () => selectChunk(index));
    chunkListEl.appendChild(li);
  });
}

function renderReferenceHistory(history, bible) {
  const currentKey = bible ? bible.current_reference_key : null;
  refListEl.innerHTML = "";

  if (!history || history.length === 0) {
    const li = document.createElement("li");
    li.className = "empty";
    li.textContent = "No references yet";
    refListEl.appendChild(li);
    return;
  }

  for (const entry of history) {
    const li = document.createElement("li");
    li.textContent = entry.display;
    if (entry.key === currentKey) li.classList.add("current");
    li.addEventListener("click", () => selectReference(entry.key));
    refListEl.appendChild(li);
  }
}

// -----------------------------------------------------------------------
// Commands — POST to the application; UI updates only via broadcast state
// -----------------------------------------------------------------------

async function postJSON(path, body) {
  try {
    const res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    if (!res.ok) {
      let msg = "Request failed.";
      try { msg = (await res.json()).error || msg; } catch { /* ignore */ }
      showError(msg);
    }
  } catch {
    showError("Network error — check the connection.");
  }
}

function togglePause() {
  if (!lastState || !lastState.transcription || !lastState.transcription.running) return;
  const path = lastState.transcription.paused
    ? "/api/transcription/resume"
    : "/api/transcription/pause";
  postJSON(path);
}

function setDisplayMode(mode) { postJSON("/api/display-mode", { mode }); }

function toggleBibleVisible() {
  const visible = !lastState || !lastState.bible || lastState.bible.visible !== false;
  postJSON("/api/bible/visibility", { visible: !visible });
}

function selectReference(key) { postJSON("/api/bible/select", { key }); }

function selectChunk(index) { postJSON("/api/bible/chunk", { index }); }

function showError(message) {
  errorToastEl.textContent = message;
  errorToastEl.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => errorToastEl.classList.add("hidden"), 3500);
}

// -----------------------------------------------------------------------
// Wire up buttons
// -----------------------------------------------------------------------

pauseBtnEl.addEventListener("click", togglePause);
modeSubsBtnEl.addEventListener("click", () => setDisplayMode("subtitles_bible"));
modeBibleBtnEl.addEventListener("click", () => setDisplayMode("bible_only"));
bibleVisibleBtnEl.addEventListener("click", toggleBibleVisible);

// -----------------------------------------------------------------------
// Start
// -----------------------------------------------------------------------

connect();
