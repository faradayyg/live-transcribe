/**
 * Live Transcript — WebSocket client
 *
 * Message types received from the Python server:
 *
 *   init            – full state snapshot (sent on every (re)connect)
 *   transcript      – one segment, final:true or final:false
 *   bible_reference – detected Bible reference + optional verse text
 *   status          – server-side transcription status string
 */

"use strict";

// -----------------------------------------------------------------------
// Config
// -----------------------------------------------------------------------

const WS_PATH            = "/ws";
const RECONNECT_BASE_MS  = 1_500;
const RECONNECT_MAX_MS   = 30_000;
const SCROLL_DEBOUNCE_MS = 80;

// How many finalized segments to keep visible in lower-third mode.
const LT_VISIBLE_SEGMENTS = 2;

// -----------------------------------------------------------------------
// Lower-third is the default. Full-transcript mode is activated by
// visiting with ?full=true (or ?full=1 / ?full=yes).
const _fullParam = new URLSearchParams(window.location.search).get("full") || "";
const isLowerThird = !["true", "1", "yes"].includes(_fullParam.toLowerCase());

if (isLowerThird) {
  document.body.classList.add("lower-third");
}

// -----------------------------------------------------------------------
// State
// -----------------------------------------------------------------------

let finalSegments = [];   // [{text, start, end}, ...]
let interimText   = "";
let socket        = null;
let reconnectDelay = RECONNECT_BASE_MS;
let scrollTimer    = null;

// -----------------------------------------------------------------------
// DOM references (resolved once at startup)
// -----------------------------------------------------------------------

const transcriptEl  = document.getElementById("transcript");
const interimEl     = document.getElementById("interim");
const biblePanelEl  = document.getElementById("bible-panel");
const bibleRefEl    = document.getElementById("bible-ref");
const bibleTextEl   = document.getElementById("bible-text");
const connDotEl     = document.getElementById("conn-dot");
const scrollEl      = document.getElementById("scroll-container");

// -----------------------------------------------------------------------
// WebSocket connection
// -----------------------------------------------------------------------

function connect() {
  const url = `ws://${window.location.host}${WS_PATH}`;
  setDot("reconnecting");

  socket = new WebSocket(url);

  socket.addEventListener("open", () => {
    reconnectDelay = RECONNECT_BASE_MS;
    setDot("connected");
  });

  socket.addEventListener("message", (event) => {
    let msg;
    try { msg = JSON.parse(event.data); }
    catch { return; }
    handleMessage(msg);
  });

  socket.addEventListener("close", () => {
    setDot("disconnected");
    socket = null;
    scheduleReconnect();
  });

  socket.addEventListener("error", () => {
    // 'close' fires right after 'error'; let that handler schedule reconnect
    socket && socket.close();
  });
}

function scheduleReconnect() {
  setTimeout(() => {
    connect();
    reconnectDelay = Math.min(reconnectDelay * 2, RECONNECT_MAX_MS);
  }, reconnectDelay);
}

// -----------------------------------------------------------------------
// Message handlers
// -----------------------------------------------------------------------

function handleMessage(msg) {
  switch (msg.type) {
    case "init":
      applyInit(msg);
      break;
    case "transcript":
      applyTranscript(msg);
      break;
    case "bible_reference":
      applyBible(msg);
      break;
    case "status":
      // Status is informational; the conn dot already reflects WS health.
      break;
    default:
      break;
  }
}

/**
 * Full state snapshot — replaces any existing transcript content.
 * Called on every (re)connect so the page is always up to date.
 */
function applyInit(msg) {
  finalSegments = (msg.segments || []).map(s => ({
    text: s.text || "",
    start: s.start ?? 0,
    end:   s.end   ?? 0,
  }));
  interimText = msg.interim || "";
  renderTranscript();

  if (msg.bible) {
    applyBible(msg.bible);
  }
}

function applyTranscript(msg) {
  if (msg.final) {
    finalSegments.push({
      text:  msg.text  || "",
      start: msg.start ?? 0,
      end:   msg.end   ?? 0,
    });
    interimText = "";
  } else {
    interimText = msg.text || "";
  }
  renderTranscript();
}

function applyBible(msg) {
  if (!msg || !msg.reference) {
    // Empty reference = clear the panel
    biblePanelEl.classList.add("hidden");
    scrollEl.classList.remove("with-bible");
    bibleRefEl.textContent  = "";
    bibleTextEl.textContent = "";
    return;
  }
  bibleRefEl.textContent  = msg.reference;
  bibleTextEl.textContent = msg.text || "";
  biblePanelEl.classList.remove("hidden");
  scrollEl.classList.add("with-bible");
}

// -----------------------------------------------------------------------
// Rendering
// -----------------------------------------------------------------------

/**
 * Rebuild the transcript area from the current state.
 *
 * Final segments are rendered as individual <p> elements.
 * The interim element is updated in place.
 * DOM is only mutated when something actually changes to avoid reflow storms.
 */
function renderTranscript() {
  if (isLowerThird) {
    // Lower-third: show only the last LT_VISIBLE_SEGMENTS finalized segments.
    // Always do a full rebuild — the visible set is small (≤2 elements).
    const visible = finalSegments.slice(-LT_VISIBLE_SEGMENTS);
    transcriptEl.innerHTML = "";
    for (const seg of visible) {
      const p = document.createElement("p");
      p.textContent = seg.text;
      transcriptEl.appendChild(p);
    }
  } else {
    // Normal mode: incremental DOM sync to avoid reflow on large transcripts.
    const existing = Array.from(transcriptEl.querySelectorAll("p"));
    const newCount  = finalSegments.length;

    for (let i = existing.length; i < newCount; i++) {
      const p = document.createElement("p");
      p.textContent = finalSegments[i].text;
      transcriptEl.appendChild(p);
    }

    for (let i = existing.length - 1; i >= newCount; i--) {
      existing[i].remove();
    }

    for (let i = 0; i < Math.min(existing.length, newCount); i++) {
      if (existing[i].textContent !== finalSegments[i].text) {
        existing[i].textContent = finalSegments[i].text;
      }
    }
  }

  // Interim line (same in both modes)
  interimEl.textContent = interimText;

  scheduleScroll();
}

// -----------------------------------------------------------------------
// Scroll to bottom (debounced)
// -----------------------------------------------------------------------

function scheduleScroll() {
  if (scrollTimer) return;
  scrollTimer = setTimeout(() => {
    scrollTimer = null;
    scrollEl.scrollTop = scrollEl.scrollHeight;
  }, SCROLL_DEBOUNCE_MS);
}

// -----------------------------------------------------------------------
// Connection dot
// -----------------------------------------------------------------------

function setDot(state) {
  connDotEl.className = state;  // "connected" | "disconnected" | "reconnecting"
}

// -----------------------------------------------------------------------
// Initialise
// -----------------------------------------------------------------------

connect();
