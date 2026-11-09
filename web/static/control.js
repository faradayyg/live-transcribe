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

const connBannerEl      = document.getElementById("conn-banner");
const statusBadgeEl     = document.getElementById("status-badge");
const statusLabelEl     = document.getElementById("status-label");
const pauseBtnEl        = document.getElementById("pause-btn");
const onAirEl           = document.getElementById("on-air");
const onAirTagEl        = document.getElementById("on-air-tag");
const onAirRefEl        = document.getElementById("on-air-ref");
const onAirTextEl       = document.getElementById("on-air-text");
const bibleVisibleBtnEl = document.getElementById("bible-visible-btn");
const chunkSectionEl    = document.getElementById("chunk-section");
const chunkPrevBtnEl    = document.getElementById("chunk-prev-btn");
const chunkNextBtnEl    = document.getElementById("chunk-next-btn");
const chunkPositionEl   = document.getElementById("chunk-position");
const chunkListEl       = document.getElementById("chunk-list");
const manualRefFormEl   = document.getElementById("manual-ref-form");
const manualRefInputEl  = document.getElementById("manual-ref-input");
const manualRefErrorEl  = document.getElementById("manual-ref-error");
const refListEl         = document.getElementById("ref-list");
const modeSubsBtnEl     = document.getElementById("mode-subs-btn");
const modeBibleBtnEl    = document.getElementById("mode-bible-btn");
const styleBtnEls       = document.querySelectorAll("[data-style]");
const errorToastEl      = document.getElementById("error-toast");

// -----------------------------------------------------------------------
// Local (non-authoritative) mirror of the last known canonical state.
// Used only to render the UI and to know what to toggle *to* on the next
// command — never trusted in place of a fresh broadcast.
// -----------------------------------------------------------------------

let lastState  = null;
let engineStatus = null;   // from "init"/"status" messages; shown only while running
let socket     = null;
let reconnectDelay = RECONNECT_BASE_MS;
let toastTimer = null;

// -----------------------------------------------------------------------
// WebSocket
// -----------------------------------------------------------------------

function connect() {
  const url = `ws://${window.location.host}${WS_PATH}`;
  setConnection("reconnecting");
  socket = new WebSocket(url);

  socket.addEventListener("open", () => {
    reconnectDelay = RECONNECT_BASE_MS;
    setConnection("connected");
  });

  socket.addEventListener("message", (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    if (msg.type === "state") applyState(msg);
    else if (msg.type === "init" || msg.type === "status") {
      engineStatus = msg.status;
      render();
    }
    // "transcript"/"bible_reference" are irrelevant to the control panel.
  });

  socket.addEventListener("close", () => {
    setConnection("disconnected");
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

function setConnection(state) {
  document.body.classList.toggle("offline", state !== "connected");
  connBannerEl.classList.toggle("hidden", state === "connected");
  connBannerEl.textContent = state === "connected"
    ? ""
    : "Lost connection to the app. Reconnecting…";
}

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
  renderDisplay(lastState.display);
  renderOnAir(lastState.bible);
  renderChunks(lastState.bible);
  renderReferenceHistory(lastState.reference_history, lastState.bible);
}

function renderTranscription(t) {
  const running = !!(t && t.running);
  const paused  = !!(t && t.paused);

  pauseBtnEl.disabled = !running;
  pauseBtnEl.classList.toggle("paused", paused);
  pauseBtnEl.textContent = paused ? "Resume" : "Pause";

  if (!running) setStatus("disconnected", "Not running");
  else if (paused) setStatus("paused", "Paused");
  else if (engineStatus === "error") setStatus("error", "Error");
  else if (engineStatus === "connecting") setStatus("connecting", "Connecting");
  else if (engineStatus === "disconnected") setStatus("error", "Engine offline");
  else setStatus("live", "Live");
}

function setStatus(state, label) {
  statusBadgeEl.className = `status-pill ${state}`;
  statusLabelEl.textContent = label;
}

function renderDisplay(display) {
  const mode = (display && display.mode) || "subtitles_bible";
  setToggle(modeSubsBtnEl, mode === "subtitles_bible");
  setToggle(modeBibleBtnEl, mode === "bible_only");

  const style = (display && display.bible_style) || "gold";
  styleBtnEls.forEach((button) => setToggle(button, button.dataset.style === style));
}

function setToggle(button, active) {
  button.classList.toggle("active", active);
  button.setAttribute("aria-pressed", String(active));
}

function renderOnAir(bible) {
  const visible = !bible || bible.visible !== false;
  const label   = bible ? (bible.current_display || bible.current_reference) : null;

  onAirEl.classList.toggle("is-hidden", !visible);
  onAirRefEl.classList.toggle("none", !label);
  onAirRefEl.textContent  = label || "Nothing selected";
  onAirTextEl.textContent = (bible && bible.current_text) || "";

  onAirTagEl.classList.toggle("hidden", visible && !label);
  onAirTagEl.classList.toggle("off", !visible);
  onAirTagEl.textContent = visible ? "Showing" : "Hidden";

  bibleVisibleBtnEl.textContent = visible ? "Hide" : "Show";
  bibleVisibleBtnEl.classList.toggle("needs-attention", !visible);
}

/** "Romans 8:1-2" -> "1-2"; the book and chapter are already shown above. */
function verseRangeOf(display) {
  return display.slice(display.lastIndexOf(":") + 1);
}

function renderChunks(bible) {
  const chunks = (bible && bible.verse_chunks) || [];
  chunkSectionEl.classList.toggle("hidden", chunks.length === 0);

  // Rebuild only when the set of pairs changes, so stepping through a
  // passage keeps keyboard focus and the list's scroll position.
  const signature = chunks.join("\n");
  if (chunkListEl.dataset.signature !== signature) {
    chunkListEl.dataset.signature = signature;
    chunkListEl.innerHTML = "";
    chunks.forEach((display, index) => {
      const li = document.createElement("li");
      const button = document.createElement("button");
      button.className = "chunk-btn";
      button.textContent = verseRangeOf(display);
      button.title = display;
      button.addEventListener("click", () => selectChunk(index));
      li.appendChild(button);
      chunkListEl.appendChild(li);
    });
  }
  if (chunks.length === 0) return;

  const currentIndex = bible.current_chunk_index;
  Array.from(chunkListEl.children).forEach((li, index) => {
    li.firstChild.classList.toggle("current", index === currentIndex);
  });

  chunkPositionEl.textContent = `${(currentIndex ?? 0) + 1} of ${chunks.length}`;
  chunkPrevBtnEl.disabled = !currentIndex;
  chunkNextBtnEl.disabled = currentIndex === chunks.length - 1;

  const currentLi = chunkListEl.children[currentIndex];
  if (currentLi) {
    chunkListEl.scrollTop = currentLi.offsetTop
      - (chunkListEl.clientHeight - currentLi.offsetHeight) / 2;
  }
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
    const button = document.createElement("button");
    button.className = "ref-btn";
    button.textContent = entry.display;
    if (entry.key === currentKey) button.classList.add("current");
    button.addEventListener("click", () => selectReference(entry.key));
    li.appendChild(button);
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
      return { ok: false, error: msg };
    }
    return { ok: true };
  } catch {
    const msg = "Network error — check the connection.";
    showError(msg);
    return { ok: false, error: msg };
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

function setBibleStyle(style) { postJSON("/api/bible/style", { style }); }

function toggleBibleVisible() {
  const visible = !lastState || !lastState.bible || lastState.bible.visible !== false;
  postJSON("/api/bible/visibility", { visible: !visible });
}

function selectReference(key) { postJSON("/api/bible/select", { key }); }

function selectChunk(index) { postJSON("/api/bible/chunk", { index }); }

function stepChunk(delta) {
  const bible  = lastState && lastState.bible;
  const chunks = (bible && bible.verse_chunks) || [];
  if (chunks.length === 0) return;
  const target = (bible.current_chunk_index ?? 0) + delta;
  if (target >= 0 && target < chunks.length) selectChunk(target);
}

async function submitManualReference(text) {
  manualRefErrorEl.classList.add("hidden");
  const result = await postJSON("/api/bible/manual", { text });
  if (result.ok) {
    manualRefInputEl.value = "";
  } else {
    // Show inline under the field as well as the toast, since this is a
    // form the operator is actively typing into.
    manualRefErrorEl.textContent = result.error;
    manualRefErrorEl.classList.remove("hidden");
  }
}

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
styleBtnEls.forEach((button) => {
  button.addEventListener("click", () => setBibleStyle(button.dataset.style));
});
chunkPrevBtnEl.addEventListener("click", () => stepChunk(-1));
chunkNextBtnEl.addEventListener("click", () => stepChunk(1));
bibleVisibleBtnEl.addEventListener("click", toggleBibleVisible);
manualRefFormEl.addEventListener("submit", (ev) => {
  ev.preventDefault();
  const text = manualRefInputEl.value.trim();
  if (text) submitManualReference(text);
});

// Arrow keys step through a passage as it is read, unless typing a reference.
document.addEventListener("keydown", (ev) => {
  if (ev.target === manualRefInputEl || ev.altKey || ev.ctrlKey || ev.metaKey) return;
  if (ev.key === "ArrowLeft") stepChunk(-1);
  else if (ev.key === "ArrowRight") stepChunk(1);
});

// -----------------------------------------------------------------------
// Start
// -----------------------------------------------------------------------

connect();
