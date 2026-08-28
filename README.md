# Live Transcriber — v0.1

A **local desktop application** for live transcription of a single speaker
(sermon, lecture, presentation).  Audio is streamed to either **Deepgram** or
**OpenAI** in real time; the transcript is displayed immediately and saved
locally as `.txt` and `.srt`.  No audio is ever saved to disk.

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
     ├── TranscriptManager  → display
     ├── BibleDetector      → reference panel  (non-blocking)
     └── SRT / TXT writers  → saved on Stop/Save
```

Key modules:

| Module | Responsibility |
|---|---|
| `audio/capture.py` | sounddevice stream → byte queue |
| `transcription/engine.py` | Abstract `TranscriptionEngine` interface |
| `transcription/deepgram_engine.py` | Deepgram WebSocket implementation |
| `transcription/openai_engine.py` | OpenAI Realtime API implementation |
| `transcription/__init__.py` | `create_engine(provider)` factory |
| `transcript/models.py` | `TranscriptSegment`, `BibleReference` dataclasses |
| `transcript/manager.py` | In-memory segment store |
| `transcript/srt.py` | SRT generation |
| `bible/parser.py` | Book name dictionary + alias resolution |
| `bible/detector.py` | Regex-based reference detection |
| `bible/bible.json` | KJV public-domain verse lookup |
| `ui/main_window.py` | PySide6 single-window UI |
| `main.py` | Entry point + dark palette |

---

## Requirements

- **Python 3.12+**
- A **Deepgram account** (free tier) and/or an **OpenAI account** with
  Realtime API access
- A working **audio input device** (built-in mic is fine for testing)
- **PortAudio** (required by sounddevice)

---

## Setup

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
cd live-transcriber
python3.12 -m venv .venv
source .venv/bin/activate      # macOS / Linux
# .venv\Scripts\activate       # Windows
```

### 4 — Install dependencies

```bash
pip install -r requirements.txt
```

### 5 — Set API key(s)

#### Deepgram

Get your key from <https://console.deepgram.com/project>.

```bash
export DEEPGRAM_API_KEY=your_deepgram_key
```

#### OpenAI

Get your key from <https://platform.openai.com/api-keys>.
Your account must have access to the **Realtime API**.

```bash
export OPENAI_API_KEY=your_openai_key
```

> You only need to set the key for the provider you plan to use.
> The app shows a warning banner if the selected provider's key is missing.

---

## Running the application

```bash
python main.py
```

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
● Live  [OpenAI]
● Live  [Deepgram]
```

The engine selector is **locked** while a session is active.  
Stop the session before switching providers.

### Starting a transcription session

1. Select your **Transcription engine**.
2. Type a **Session name** (used for the saved file names).
3. Click **▶ Start**.
4. The status badge changes: *Connecting → Live*.
5. Speak — transcript appears in real time.
   - **Grey italic** text = current unfinished interim segment.
   - **White** text = finalized transcript.

### Pausing and resuming

Click **⏸ Pause** to temporarily mute the microphone.  
Click **▶ Resume** to continue.

### Stopping a session

Click **⏹ Stop**.  This disconnects from the provider and stops audio capture.

### Saving the transcript

After stopping, click **💾 Save Transcript** and choose a folder.  
Two files are written:

```
<session-name>.txt   — readable plain text
<session-name>.srt   — subtitle file with timestamps
```

---

## Switching providers

You can run the same lecture through both providers to compare quality:

1. Run the session with **Deepgram**.  Save as `session-deepgram.txt`.
2. Click **⏹ Stop**.
3. Change the engine to **OpenAI** in the dropdown.
4. Start a new session.  Save as `session-openai.txt`.

Both providers use the same GUI, Bible detector, web output, TXT, and SRT
pipeline — so transcripts are directly comparable.

---

## Provider differences

| Feature | Deepgram | OpenAI |
|---|---|---|
| Model | nova-2 | gpt-4o-transcribe |
| Interim results | Yes (word-level) | Yes (delta events) |
| Final results | Yes | Yes |
| Timestamps | Per-word from API | Wall-clock (monotonic) |
| Bible references | Works | Works |
| Web output | Works | Works |
| SRT | Works | Works (approx. timestamps) |

OpenAI timestamps in the SRT are derived from wall-clock time because the
Realtime API does not return per-word timestamps.  The segments are still
time-ordered and accurate to within the VAD silence window (~500 ms).

---

## Bible reference detection

When the transcript contains a Bible reference the **Bible Reference** panel
updates automatically with the detected reference and (where available) the
KJV verse text.

Supported forms:

```
John 3:16
Romans 8:1-4
Psalm 23
1 Corinthians 13
Matthew 5:3-12
John chapter 3 verse 16
Romans chapter 8 verses 1 through 4
First Corinthians chapter 13
```

Bible detection runs after finalisation and **never blocks transcription**.

The bundled `bible/bible.json` contains a curated set of commonly cited KJV
verses (public domain).  To add more verses, extend the `"verses"` object
using the key format `"Book:Chapter:Verse"`.

---

## Testing with a microphone

Before connecting to a sound system:

1. Start the app.
2. Select your built-in microphone.
3. Choose your preferred engine (Deepgram or OpenAI).
4. Name the session `test`.
5. Click **▶ Start** and speak clearly for 30 seconds.
6. Verify the live transcript updates as you speak.
7. Reference a Bible verse aloud (e.g. "John chapter 3 verse 16").
8. Check the Bible Reference panel updates.
9. Click **⏹ Stop**, then **💾 Save**.
10. Open the saved `.txt` and `.srt` files and confirm correctness.

---

## Running automated tests

```bash
pytest tests/ -v
```

The tests cover:
- Bible reference parsing and detection (written + spoken forms)
- Invalid / empty references
- SRT timestamp formatting
- SRT file generation
- TranscriptManager state management
- Engine interface conformance (Deepgram + OpenAI)
- Provider factory
- OpenAI event handling (interim, final, errors)
- Missing API key error paths
- Web server output

Audio capture and live API integration require a live microphone and API key
and are validated manually using the procedure above.

---

## Troubleshooting

| Problem | Solution |
|---|---|
| "No input devices found" | Check system audio settings; try clicking ↺ Refresh |
| Status stays "Connecting" | Check your internet connection and API key |
| "DEEPGRAM_API_KEY is not set" | Export the variable before launching (see Setup §5) |
| "OPENAI_API_KEY is not set" | Export the variable before launching (see Setup §5) |
| OpenAI auth failed | Verify your key has Realtime API access at platform.openai.com |
| OpenAI "access denied" | Your key may not have gpt-4o-transcribe Realtime access |
| App freezes on Start | Make sure PortAudio is installed (`brew install portaudio`) |
| Garbled / no transcript | Speak closer to the mic; check the level meter shows activity |
| SRT timestamps off | OpenAI uses wall-clock times; Deepgram uses per-word API times |

---

## Known limitations (v0.1)

1. **English only** — The model is fixed to `nova-2` / `gpt-4o-transcribe`.
2. **Single speaker** — Speaker diarization is not implemented.
3. **No reconnection** — If the WebSocket drops, restart the session.
4. **Bible verse coverage** — Only commonly cited KJV verses are bundled;
   most references will show the reference without the verse text.
5. **No local backup** — If the app crashes mid-session, unsaved transcript
   is lost (no auto-save).
6. **macOS only tested** — Windows/Linux may need minor PortAudio tweaks.
7. **OpenAI SRT timestamps** — Approximate (wall-clock relative); Deepgram
   provides more precise word-level timestamps.

---

## Suggestions for v0.2

> These are *not* implemented.  This list is for planning only.

- **Automatic reconnection** on WebSocket disconnect.
- **Auto-save** in-progress transcript to a temp file every 30 seconds.
- **Language selection** in the UI.
- **Font size control** in the transcript panel.
- **Full KJV / ESV / NIV data file** loader (user supplies their own `.json`).
- **Export to DOCX** alongside TXT/SRT.
- **Keyword highlighting** — mark repeated words or phrases in the transcript.
- **Speaker diarization** — once Deepgram stabilises the feature in `nova-2`.
- **Confidence colouring** — shade low-confidence words differently.
- **Session history** — list previously saved sessions on a sidebar.

---


## Project structure

```
live-transcriber/
├── main.py                     Entry point
├── requirements.txt
├── README.md
├── ui/
│   └── main_window.py          PySide6 single-window UI
├── audio/
│   └── capture.py              sounddevice audio capture
├── transcription/
│   ├── engine.py               Abstract TranscriptionEngine
│   └── deepgram_engine.py      Deepgram WebSocket implementation
├── transcript/
│   ├── models.py               TranscriptSegment, BibleReference
│   ├── manager.py              In-memory transcript store
│   └── srt.py                  SRT generation
├── bible/
│   ├── parser.py               Book name dictionary + alias resolution
│   ├── detector.py             Regex-based reference detector
│   └── bible.json              KJV public-domain verse lookup
├── sessions/                   Default save location (local)
└── tests/
    ├── test_bible.py
    ├── test_srt.py
    └── test_transcript.py
```

---

## License

This application and its source code are for personal use.  
KJV verse text included in `bible/bible.json` is in the public domain.
