/**
 * Live Transcript — WebSocket client
 *
 * Default mode  : single 10:2 overlay slot — one paragraph at a time.
 *   Bible verse completely replaces the subtitle when active.
 *   Background is transparent for use as an OBS/vMix browser source.
 *   Set the browser source to a 10:2 resolution (e.g. 1920 × 384).
 *
 * Full mode (?full=true) : dark scrolling page — all paragraphs, for
 *   monitoring or editorial review.
 */

"use strict";

// -----------------------------------------------------------------------
// Config
// -----------------------------------------------------------------------

const WS_PATH           = "/ws";
const RECONNECT_BASE_MS = 1_500;
const RECONNECT_MAX_MS  = 30_000;

// -----------------------------------------------------------------------
// Mode detection
// -----------------------------------------------------------------------

const _fullParam  = new URLSearchParams(window.location.search).get("full") || "";
const isFullMode  = ["true", "1", "yes"].includes(_fullParam.toLowerCase());

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

let finalSegments  = [];   // [{text, start, end}, …]
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
} else {
  // Overlay mode — slot is already visible; full container stays hidden
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
    case "init":           applyInit(msg);       break;
    case "transcript":     applyTranscript(msg); break;
    case "bible_reference": applyBible(msg);     break;
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
    render();
    return;
  }
  bibleRefEl.textContent  = msg.reference;
  bibleTextEl.textContent = msg.text || "";
  bibleActive = true;
  render();
}

// -----------------------------------------------------------------------
// Render
// -----------------------------------------------------------------------

function render() {
  if (isFullMode) {
    renderFull();
  } else {
    renderOverlay();
  }
}

/**
 * Overlay mode — single slot, one paragraph, bible takes precedence.
 */
function renderOverlay() {
  if (bibleActive) {
    viewSubtitleEl.classList.add("hidden");
    viewBibleEl.classList.remove("hidden");
    return;
  }

  viewBibleEl.classList.add("hidden");
  viewSubtitleEl.classList.remove("hidden");

  // Show only the most recent finalised paragraph
  const latest = finalSegments.length > 0
    ? finalSegments[finalSegments.length - 1].text
    : "";

  currentTextEl.textContent = latest;
  interimTextEl.textContent = interimText;
}

/**
 * Full mode — scrolling history of all paragraphs.
 */
function renderFull() {
  // Incremental DOM sync
  const existing = Array.from(transcriptEl.querySelectorAll("p"));
  const count    = finalSegments.length;

  for (let i = existing.length; i < count; i++) {
    const p = document.createElement("p");
    p.textContent = finalSegments[i].text;
    transcriptEl.appendChild(p);
  }
  for (let i = existing.length - 1; i >= count; i--) {
    existing[i].remove();
  }
  for (let i = 0; i < Math.min(existing.length, count); i++) {
    if (existing[i].textContent !== finalSegments[i].text) {
      existing[i].textContent = finalSegments[i].text;
    }
  }

  fullInterimEl.textContent = interimText;
  fullContainerEl.scrollTop = fullContainerEl.scrollHeight;
}

// -----------------------------------------------------------------------
// Connection dot
// -----------------------------------------------------------------------

function setDot(state) {
  connDotEl.className = state;
}

// -----------------------------------------------------------------------
// Start
// -----------------------------------------------------------------------

connect();

