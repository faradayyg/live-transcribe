# Live Transcriber

A **local desktop application** for live transcription of a single speaker
(sermon, lecture, presentation). Audio is streamed to either **Deepgram** or
**OpenAI** in real time; the transcript is displayed immediately and saved
locally as `.txt` and `.srt`. No audio is ever saved to disk.

---

## Architecture

```
Audio Input (sounddevice)
        │  PCM mono 16-bit 16 kHz
        ▼
 TranscriptionWorker (QThread)
        │  raw bytes
        ▼
  Selected Engine (WebSocket)
  ┌─────┴────────┐
  Deepgram     OpenAI
  └─────┬────────┘
        │  TranscriptSegment (normalised)
        ▼
  MainWindow (Qt main thread)
     ├── TranscriptManager       → live display + TXT/SRT on save
     ├── TranscriptContextBuffer → rolling 20-second context window
     │        │
     │        ▼  (when candidate detected)
     │   BibleResolverWorker     → debounce → background LLM call
     │        │  list[BibleReference]
     │        ▼
     │   ReferenceHistory        → session history, deduplication
     │        │
     │        ▼
     │   Bible panel (history list + verse display)
     └── WebOutputServer         → HTTP + WebSocket → browser overlay
```

Key modules:

| Module | Responsibility |
|---|---|
| `audio/capture.py` | sounddevice stream → byte queue |
| `transcription/engine.py` | Abstract `TranscriptionEngine` interface |
| `transcription/deepgram_engine.py` | Deepgram WebSocket implementation |
| `transcription/openai_engine.py` | OpenAI Realtime API (`gpt-live-transcribe`) |
| `transcription/__init__.py` | `create_engine(provider)` factory |
| `transcript/models.py` | `TranscriptSegment`, `BibleReference` dataclasses |
| `transcript/manager.py` | In-memory segment store |
| `transcript/srt.py` | SRT generation |
| `bible/parser.py` | 66-book dictionary + alias/spoken-form resolution |
| `bible/detector.py` | Regex candidate detector + `detect_all()` fallback |
| `bible/context.py` | `TranscriptContextBuffer` (rolling window), `ReferenceHistory` |
| `bible/resolver.py` | LLM resolver (`gpt-4o-mini`), `BibleResolverWorker` (Qt async) |
| `bible/config.py` | Environment-variable configuration for the Bible system |
| `bible/KJV/bible.json` | Complete KJV Bible (public domain) |
| `web/server.py` | aiohttp HTTP + WebSocket server for browser overlay |
| `web/static/` | Output page HTML/CSS/JS (lower-third + full-transcript modes) |
| `ui/main_window.py` | PySide6 single-window UI |
| `main.py` | Entry point, `.env` loading, logging setup |

---

## Requirements

- **Python 3.12+**
- A **Deepgram account** (free tier) and/or an **OpenAI account** with
  Realtime API access
- A working **audio input device** (built-in mic is fine for testing)
- **PortAudio** (required by sounddevice)

---

## Windows Setup

> **Two double-clicks** is all a Windows user needs after downloading the project.

### 1. Install Python 3.12 or newer

Download from **<https://www.python.org/downloads/>**

During installation, tick **"Add Python to PATH"** (or "Add python.exe to PATH").

Verify the install:
```
py --version
```

### 2. Run setup

Double-click **`setup.bat`** in the project folder.

Setup will:
- Check that Python 3.12+ is available
- Create a `.venv` virtual environment (only on first run)
- Install all Python dependencies
- Verify that audio (sounddevice / PortAudio) works
- Create `.env` from the template (only if `.env` does not already exist)
- Create `logs\` and `sessions\` directories

> **PortAudio**: `sounddevice` bundles the PortAudio DLLs for Windows inside
> the Python package. You do **not** need to install PortAudio separately.

### 3. Configure API keys

Open **`.env`** (any text editor) and enter your key(s):

```
DEEPGRAM_API_KEY=your_deepgram_key_here
OPENAI_API_KEY=your_openai_key_here
```

You only need to set the key for the provider you plan to use.

### 4. Start the application

Double-click **`run.bat`**.

The application window opens. The console window stays open alongside it —
it shows log output and any error messages.

---

## Setup (macOS / Linux)

### 1 — Install Python 3.12+

macOS (Homebrew):
```bash
brew install python@3.12
```

Windows: download from <https://python.org>.

### 2 — Install PortAudio (macOS / Linux)

```bash
# macOS
brew install portaudio

# Ubuntu / Debian
sudo apt-get install portaudio19-dev python3-dev
```

### 3 — Create a virtual environment

```bash
cd live-transcribe
python3.12 -m venv .venv
source .venv/bin/activate      # macOS / Linux
```

### 4 — Install dependencies

```bash
pip install -r requirements.txt
```

### 5 — Configure environment

Copy the template and fill in your values:

```bash
cp .env.template .env
```

Then edit `.env`:

```dotenv
# Required for Deepgram engine
DEEPGRAM_API_KEY=your_deepgram_key

# Required for OpenAI engine and/or Bible LLM resolver
OPENAI_API_KEY=your_openai_key
```

`.env` is git-ignored and never committed. You only need to set the key(s)
for the provider(s) you plan to use. The app shows a warning banner if the
selected provider's key is missing.

> Shell exports always take priority over `.env`. If `OPENAI_API_KEY` is
> already in your environment, `.env` will not overwrite it.

---

## Running the application

**Windows:** double-click `run.bat`

**macOS / Linux:**
```bash
python main.py
```

Logs are written to `logs/live_transcriber.log` (rotating, 5 MB × 3 files)
and echoed to stderr. Set `LOG_LEVEL=DEBUG` in `.env` for verbose output.

---

## Using the application

### Selecting an audio input

1. The **Audio Input** dropdown lists all detected input devices.
2. Select the device you want to use (e.g. "Built-in Microphone").
3. Click **↺ Refresh** if you plugged in a device after launch.

### Selecting a transcription engine

In the **Session** panel, use the **Transcription engine** dropdown to choose:

- **Deepgram** — requires `DEEPGRAM_API_KEY`
- **OpenAI** — requires `OPENAI_API_KEY`

The status badge always shows the active provider:

```
● Live  [Deepgram]
● Live  [OpenAI]
```

The engine selector is **locked** while a session is active.
Stop the session before switching providers.

### Starting a session

1. Select your **Transcription engine**.
2. Type a **Session name** (used for the saved file names).
3. Click **▶ Start**.
4. The status badge changes: *Connecting → Live*.
5. Speak — transcript appears in real time.
   - **Grey italic** text = current unfinished interim segment.
   - **White** text = finalised transcript.

### Pausing and resuming

Click **⏸ Pause** to mute the microphone temporarily.
Click **▶ Resume** to continue.

### Stopping a session

Click **⏹ Stop**. This disconnects from the provider and stops audio capture.

### Saving the transcript

After stopping, click **💾 Save Transcript** and choose a folder.
Two files are written:

```
<session-name>.txt   — readable plain text
<session-name>.srt   — subtitle file with timestamps
```

---

## Provider differences

| Feature | Deepgram | OpenAI |
|---|---|---|
| Model | nova-2 | gpt-live-transcribe |
| Interim results | Yes (word-level) | Yes (delta events) |
| Final results | Yes | Yes |
| Timestamps | Per-word from API | Wall-clock (monotonic) |
| Bible references | Works | Works |
| Web output | Works | Works |
| SRT | Works | Works (approx. timestamps) |

OpenAI timestamps in the SRT are derived from wall-clock time because the
Realtime API does not return per-word timestamps. The segments are still
time-ordered and accurate to within the VAD silence window (~500 ms).

---

## Bible reference detection

When the transcript contains a Bible reference, the **Detected Scripture**
panel updates automatically. Detection uses a hybrid approach:

```
Finalised transcript segment
         ↓
 TranscriptContextBuffer     rolling 20-second window
         ↓
 is_candidate()              quick local check (book names + keywords)
         ↓  (if candidate)
 BibleResolverWorker         debounce (800 ms) → background LLM call
         ↓
 gpt-4o-mini                 structured JSON response
         ↓
 ReferenceHistory             dedup + upgrade (chapter-only → verse-specific)
         ↓
 Bible panel + verse text
```

The LLM **never blocks transcription** — it runs in a background thread pool.
If the LLM is unavailable or fails, the local regex detector (`detect_all()`)
is used as a fallback.

### Supported reference forms

```
John 3:16                          John chapter 3 verse 16
Romans 8:1-4                       Romans chapter 8 verses 1 through 4
Psalm 23                           First Corinthians chapter 13
1 Corinthians 13:4-7               John 3:16, 17, 18, and 19
Matthew 5:3-12
```

### Reference history panel

All detected references accumulate in a **session history list** (newest
first). Click any entry to display its Bible passage without changing the
history.

### Configuration

All Bible resolver settings can be overridden in `.env`:

| Variable | Default | Description |
|---|---|---|
| `BIBLE_LLM_ENABLED` | `true` | Set to `false` to use local regex only |
| `BIBLE_LLM_MODEL` | `gpt-4o-mini` | OpenAI model for reference extraction |
| `BIBLE_REFERENCE_CONFIDENCE_THRESHOLD` | `0.85` | Minimum confidence to accept a reference |
| `BIBLE_CONTEXT_SECONDS` | `20.0` | Rolling context window (seconds) |
| `BIBLE_DEBOUNCE_MS` | `800` | Delay before sending candidate to LLM (ms) |

### Bible text

The complete KJV Bible (`bible/KJV/bible.json`) is used for verse lookup.
KJV text is in the public domain. The code is structured so that a different
translation can be dropped in by replacing the JSON file.

---

## Web output

A local HTTP + WebSocket server runs on `http://localhost:8765` and provides
a browser overlay suitable for OBS, Wirecast, or any browser-source input.

Two modes are available (shown as clickable URLs in the left panel):

| URL | Mode |
|---|---|
| `http://localhost:8765` | **Lower-third** — last ~3 lines of transcript + current Scripture |
| `http://localhost:8765?full=true` | **Full transcript** — scrolling complete transcript |

The page auto-reconnects if the app is restarted. The current Scripture
display updates whenever the operator selects a reference in the history panel.

---

## Logging

Logs are written to `logs/live_transcriber.log`:

- Rotating file: 5 MB per file, 3 backups kept (15 MB ceiling)
- Also echoed to stderr during development
- Format: `2026-08-29 09:52:58  INFO  bible.resolver  LLM resolved Romans 8:1-4`

Set `LOG_LEVEL=DEBUG` in `.env` to enable verbose output from all modules.

---

## Running automated tests

```bash
pytest tests/ -v
```

138 tests covering:

- Bible reference parsing (written + spoken forms, ranges, rapid-fire)
- `is_candidate()` gate and `_parse_response()` normalisation
- LLM resolver with mocked OpenAI responses (all reference types)
- Cross-segment context and continuation
- `ReferenceHistory` deduplication and upgrade
- SRT timestamp formatting and file generation
- `TranscriptManager` state management
- Engine interface conformance (Deepgram + OpenAI)
- Provider factory
- OpenAI event handling (interim, final, errors)
- Missing API key error paths
- Web server broadcast and init state

Audio capture and live API calls require a microphone and API key and are
validated manually.

---

## Troubleshooting

| Problem | Solution |
|---|---|
| "No input devices found" | Check system audio settings; click ↺ Refresh |
| Status stays "Connecting" | Check your internet connection and API key in `.env` |
| Warning banner at startup | Add the missing API key to `.env` |
| OpenAI auth failed | Verify your key has Realtime API access at platform.openai.com |
| Bible references not detected | Check `OPENAI_API_KEY` is set; try `BIBLE_LLM_ENABLED=false` to test fallback |
| Bible LLM resolver slow | Normal — LLM call is async; transcription is never blocked |
| App freezes on Start (macOS) | Make sure PortAudio is installed (`brew install portaudio`) |
| Garbled / no transcript | Speak closer to the mic; check the level meter shows activity |
| SRT timestamps off | OpenAI uses wall-clock times; Deepgram provides per-word API times |
| Web overlay not updating | Confirm the app is running; the page auto-reconnects after ~3 s |

---

## Windows troubleshooting

### Python not found

Open Command Prompt and run:
```
py --version
```
or:
```
python --version
```

If neither is found, Python is not installed or not on PATH.
Download from <https://www.python.org/downloads/> and tick **"Add Python to PATH"** during installation.

### Python version too old

`setup.bat` requires Python 3.12 or newer. If an older version is installed,
download a newer one from <https://www.python.org/downloads/>. Multiple Python
versions can coexist on Windows — the `py` launcher picks the right one.

### Dependency installation failed

Run `setup.bat` from a **Command Prompt** (not by double-clicking) so you can
scroll up to see the full error:

```
cd C:\path\to\live-transcribe
setup.bat
```

The error message will usually identify the failing package.

### No audio devices

1. Check that Windows has granted microphone access:
   **Settings → Privacy & Security → Microphone** — ensure apps can access the microphone.
2. Check **Sound Settings → Input** — confirm a microphone is listed and not disabled.
3. Click the ↺ Refresh button in the app after connecting a device.

### Audio capture fails (sounddevice error)

`sounddevice` bundles PortAudio DLLs for Windows — no separate install is needed.
If you see a PortAudio error, the **Microsoft Visual C++ Redistributable** may be missing.
Download and install it from:
<https://aka.ms/vs/17/release/vc_redist.x64.exe>

### API key missing / warning banner

Open `.env` in Notepad and add your key:
```
DEEPGRAM_API_KEY=your_key_here
```
or:
```
OPENAI_API_KEY=your_key_here
```
Save the file, then restart the application.

### Application won't start / crashes immediately

Run from Command Prompt to see the full error output:
```
cd C:\path\to\live-transcribe
run.bat
```
Or double-click **`run_debug.bat`** — it sets `LOG_LEVEL=DEBUG` and keeps the
console open so the full error is visible.

### `.venv` missing when running `run.bat`

Run `setup.bat` first. The application cannot start without the virtual environment.

---

## Known limitations

1. **English only** — The transcription model is fixed to `nova-2` (Deepgram)
   / `gpt-live-transcribe` (OpenAI).
2. **Single speaker** — Speaker diarization is not implemented.
3. **No reconnection** — If the WebSocket drops, stop and restart the session.
4. **No auto-save** — If the app crashes mid-session, unsaved transcript is
   lost.
5. **macOS only tested** — Windows/Linux may need minor PortAudio tweaks.
6. **OpenAI SRT timestamps** — Approximate (wall-clock relative); Deepgram
   provides more precise word-level timestamps.

---

## Suggestions for future versions

- Automatic WebSocket reconnection on disconnect.
- Auto-save in-progress transcript every 30 seconds.
- Language selection in the UI.
- Font size control in the transcript panel.
- Export to DOCX alongside TXT/SRT.
- Speaker diarization once provider support stabilises.
- Session history — list previously saved sessions in a sidebar.

---

## Project structure

```
live-transcribe/
├── main.py                     Entry point, .env loading, logging setup
├── requirements.txt
├── .env.template               Configuration template — copy to .env
├── setup.bat                   Windows: one-time setup (double-click)
├── run.bat                     Windows: launch the application
├── run_debug.bat               Windows: launch with LOG_LEVEL=DEBUG
├── README.md
├── audio/
│   └── capture.py              sounddevice audio capture
├── transcription/
│   ├── engine.py               Abstract TranscriptionEngine
│   ├── deepgram_engine.py      Deepgram WebSocket implementation
│   └── openai_engine.py        OpenAI Realtime API (gpt-live-transcribe)
├── transcript/
│   ├── models.py               TranscriptSegment, BibleReference dataclasses
│   ├── manager.py              In-memory segment store
│   └── srt.py                  SRT generation
├── bible/
│   ├── parser.py               66-book dictionary + alias resolution
│   ├── detector.py             Regex detector (candidate gate + fallback)
│   ├── context.py              TranscriptContextBuffer, ReferenceHistory
│   ├── resolver.py             LLM resolver + BibleResolverWorker (Qt async)
│   ├── config.py               Environment-variable configuration
│   └── KJV/bible.json          Complete KJV Bible (public domain)
├── web/
│   ├── server.py               aiohttp HTTP + WebSocket server
│   └── static/                 output.html, style.css, app.js
├── ui/
│   └── main_window.py          PySide6 single-window UI
├── logs/                       Rotating log files (git-ignored)
├── sessions/                   Default save location (git-ignored)
└── tests/
    ├── test_bible.py           Book parsing + basic detection
    ├── test_bible_context.py   Context buffer + history + detect_all
    ├── test_bible_resolver.py  LLM resolver (mocked), is_candidate, parse
    ├── test_engines.py         Engine interface + provider factory
    ├── test_srt.py             SRT generation
    ├── test_transcript.py      TranscriptManager
    └── test_web.py             Web server
```

---

## License

This application and its source code are for personal use.
KJV text in `bible/KJV/bible.json` is in the public domain.
