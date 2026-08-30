/**
 * Live Transcript -- WebSocket client
 *
 * Modes (selected by URL query parameter):
 *
 *   (default)    Overlay: 10:2 slot, one subtitle at a time.
 *                Bible verse replaces subtitle when active.
 *                Transparent background for OBS browser source.
 *
 *   ?full=true   Full transcript: dark scrolling page, all paragraphs.
 *                For monitoring or editorial review.
 *
 *   ?bible=true  Bible-only: shows the current verse/reference only.
 *                No subtitle displayed at all.
 *                Transparent background; use as a separate OBS source.
 */

"use strict";

const WS_PATH           = "/ws";
const RECONNECT_BASE_MS = 1_500;
const RECONNECT_MAX_MS  = 30_000;

// -----------------------------------------------------------------------
// Mode detection
// -----------------------------------------------------------------------

const _params     = new URLSearchParams(window.location.search);
const _truthy     = v => ["true", "1", "yes"].includes((v || "").toLowerCase());
const isFullMode  = _truthy(_params.get("full"));
const isBibleMode = _truthy(_params.get("bible"));

// -----------------------------------------------------------------------
// DOM references
// -----------------------------------------------------------------------

const slotEl          = document.getElementById("slot");
const viewSubtitleEl  = document.getElementById("view-subtitle");
const viewBibleEl     = document.getElementById("view-bible");
const currentTextEl   = document.getElementById("current-text");
const interimTextEl   = document.getElementById("interim-text");
const bibleRefEl      = document.getElementById("bible-ref");
const bibleTextEl     = document.getElementById("bible-text");

const fullContainerEl = document.getElementById("full-container");
const transcriptEl    = document.getElementById("transcript");
const fullInterimEl   = document.getElementById("full-interim");

const connDotEl       = document.getElementById("conn-dot");

// -----------------------------------------------------------------------
// State
// -----------------------------------------------------------------------

let finalSegments  = [];
let interimText    = "";
let bibleActive    = false;
let socket         = null;
let reconnectDelay = RECONNECT_BASE_MS;

// -----------------------------------------------------------------------
// Initialise layout
// -----------------------------------------------------------------------

if (isFullMode) {
  slotEl.classList.add("hidden");
  fullContainerEl.classList.remove("hidden");
} else if (isBibleMode) {
  document.body.classList.add("bible-only");
  fullContainerEl.classList.add("hidden");
  // Subtitle view is permanently hidden in this mode
  viewSubtitleEl.classList.add("hidden");
} else {
  // Default overlay mode
  fullContainerEl.classList.add("hidden");
}

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
    handleMessage(msg);
  });

  socket.addEventListener("close", () => {
    setDot("disconnected");
    socket = null;
    setTimeout(() => {
      connect();
      reconnectDelay = Math.min(reconnectDelay * 2, RECONNECT_MAX_MS);
    }, reconnectDelay);
  });

  socket.addEventListener("error", () => { socket && socket.close(); });
}

// -----------------------------------------------------------------------
// Message dispatch
// -----------------------------------------------------------------------

function handleMessage(msg) {
  switch (msg.type) {
    case "init":            applyInit(msg);       break;
    case "transcript":      applyTranscript(msg); break;
    case "bible_reference": applyBible(msg);      break;
    default: break;
  }
}

function applyInit(msg) {
  finalSegments = (msg.segments || []).map(s => ({
    text: s.text || "", start: s.start ?? 0, end: s.end ?? 0,
  }));
  interimText = msg.interim || "";
  render();
  if (msg.bible) applyBible(msg.bible);
}

function applyTranscript(msg) {
  if (isBibleMode) return;   // transcript is irrelevant in bible-only mode
  if (msg.final) {
    finalSegments.push({ text: msg.text || "", start: msg.start ?? 0, end: msg.end ?? 0 });
    interimText = "";
  } else {
    interimText = msg.text || "";
  }
  render();
}

function applyBible(msg) {
  if (!msg || !msg.reference) {
    bibleActive = false;
  } else {
    bibleRefEl.textContent  = msg.reference;
    bibleTextEl.textContent = msg.text || "";
    bibleActive = true;
  }
  render();
}

// -----------------------------------------------------------------------
// Render
// -----------------------------------------------------------------------

function render() {
  if (isFullMode)  { renderFull();      return; }
  if (isBibleMode) { renderBibleOnly(); return; }
  renderOverlay();
}

/** Default overlay: subtitle + bible share one slot; bible takes precedence. */
function renderOverlay() {
  if (bibleActive) {
    viewSubtitleEl.classList.add("hidden");
    viewBibleEl.classList.remove("hidden");
    return;
  }
  viewBibleEl.classList.add("hidden");
  viewSubtitleEl.classList.remove("hidden");
  currentTextEl.textContent = finalSegments.length
    ? finalSegments[finalSegments.length - 1].text : "";
  interimTextEl.textContent = interimText;
}

/** Bible-only: show the verse when active, show nothing when cleared. */
function renderBibleOnly() {
  if (bibleActive) {
    viewBibleEl.classList.remove("hidden");
  } else {
    viewBibleEl.classList.add("hidden");
  }
}

/** Full-transcript: scrolling history of all paragraphs. */
function renderFull() {
  const existing = Array.from(transcriptEl.querySelectorAll("p"));
  const count    = finalSegments.length;
  for (let i = existing.length; i < count; i++) {
    const p = document.createElement("p");
    p.textContent = finalSegments[i].text;
    transcriptEl.appendChild(p);
  }
  for (let i = existing.length - 1; i >= count; i--) existing[i].remove();
  for (let i = 0; i < Math.min(existing.length, count); i++) {
    if (existing[i].textContent !== finalSegments[i].text)
      existing[i].textContent = finalSegments[i].text;
  }
  fullInterimEl.textContent = interimText;
  fullContainerEl.scrollTop = fullContainerEl.scrollHeight;
}

// -----------------------------------------------------------------------
// Connection dot
// -----------------------------------------------------------------------

function setDot(state) { connDotEl.className = state; }

// -----------------------------------------------------------------------
// Start
// -----------------------------------------------------------------------

connect();
