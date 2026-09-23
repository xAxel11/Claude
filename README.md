# J.A.R.V.I.S. — desktop AI assistant

A full-screen, Iron-Man style assistant in Python. You talk to it with your **mic** or by **typing**,
it answers in a **free Microsoft neural voice** (British "Ryan" by default), and it thinks with
**Gemini (free)** or **ChatGPT**. Switch between them in the HUD's *AI CORE* switch or by saying
"switch to ChatGPT". It can also see and control your computer.

![layout](https://img.shields.io/badge/HUD-fullscreen-3ee6ff)

## What it can do

| Area | Examples |
|---|---|
| Conversation | "Jarvis, explain black holes", "what's the latest on the Artemis mission?" (live web search) |
| Map | "Show me Tokyo", "satellite view of the Eiffel Tower", "route to the airport" (drawn on the map with distance and time), "where am I?", "pin Paris and Rome", "expand the map" |
| Code | "write a snake game in Python and run it", "make a script that renames my photos", "open it in VS Code". Jarvis writes the file, runs it, reads the errors and fixes them |
| Camera sensors | "what do your sensors see?", "how far away am I?", "what am I holding?". Face tracking with target brackets, distance estimate, motion and light meters, and "welcome back" when you return |
| AI switch | "switch to ChatGPT" / "switch to Gemini", or the AI CORE switch at the top |
| Mouse & keyboard | "move the mouse to the top-left", "click the Send button", "type hello world", "press ctrl+t" |
| Screen vision | "what's on my screen?", "read me that error message" |
| Camera | "turn on the camera", "what am I holding?", "take a photo" |
| Apps & system | "open Spotify", "open youtube.com", "play lo-fi on YouTube", "volume 40", "next track", "system status" |
| Files & shell | "list my Downloads folder", "create notes.txt with…", "run `ipconfig`" (asks first) |
| Email | "read my unread emails", "email bob@example.com that I'm running late" (asks first) |
| Instagram | "log in to Instagram", "take a photo and post it with the caption 'Lab day'", "DM @friend hi" (asks first) |
| Memory & timers | "remember my sister's email is …", "set a 10 minute timer for the pasta" |

The HUD shows:
- an animated arc reactor whose colour shows the state: cyan idle, green listening, amber thinking;
- the AI CORE switch and the model in use;
- live CPU, RAM, disk, battery and network stats;
- the webcam feed with the sensor overlay;
- the holographic map, with a radar sweep, crosshair, coordinates, cyan pins and routes, and
  HUD, Road, Satellite, Hybrid and Terrain styles (⛶ expands it, ✕ clears it);
- the last screenshot or photo;
- a pop-up window for code Jarvis writes;
- a log of everything Jarvis says and does.

## Windows quick start

1. Install Python 3.10+ from https://www.python.org/downloads/ and tick **"Add python.exe to PATH"**.
2. Double-click **`Jarvis.bat`**. The first run installs everything, which takes a few minutes.
3. Notepad opens `.env`. Paste your Gemini key after `GEMINI_API_KEY=`, save, and close Notepad. Jarvis then starts.

If packages ever break, run `Reinstall.bat`.

## Setup (manual / Linux / macOS)

Needs Python 3.10 or newer.

```bash
git clone <this repo> && cd <repo>
python -m venv .venv
# Windows: .venv\Scripts\activate      Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env        # Windows: copy .env.example .env
# edit .env and paste your Gemini key into GEMINI_API_KEY
python run.py
```

Get a free Gemini key at https://aistudio.google.com/apikey, and/or an OpenAI key at
https://platform.openai.com/api-keys.

### Extra system packages

- **Linux (Ubuntu, Pop!_OS, Debian):**
  `sudo apt install python3-tk python3-dev portaudio19-dev espeak-ng`
  Mouse and keyboard control and screenshots need an **X11 session**. Under Wayland,
  choose "Xorg" on the login screen or those tools won't work. Everything else still works.
- **macOS:** `brew install portaudio`, then allow your terminal in System Settings, under
  Privacy & Security, for Accessibility, Screen Recording, Microphone and Camera.
- **Windows:** `pip install -r requirements.txt` is usually enough.

## Controls

| Key or control | Action |
|---|---|
| Type + Enter | Send a text command |
| 🎤 MIC / Ctrl+M | Speak one command |
| Wake word switch | Hands-free: say "Jarvis, …" anytime |
| ■ STOP | Stop talking |
| F11 / Esc | Toggle / leave fullscreen |
| Ctrl+Q | Quit |
| Mouse to a screen corner | Emergency stop for mouse/keyboard automation (PyAutoGUI failsafe) |

## Email

For Gmail, turn on 2-step verification, create an **App Password** at
https://myaccount.google.com/apppasswords, and put it in `EMAIL_PASSWORD` (your normal
password will not work). Other providers work if you set `SMTP_HOST`, `SMTP_PORT` and `IMAP_HOST`.

## Instagram: read this first

Instagram has no official API for personal accounts, so this uses the unofficial
[`instagrapi`](https://github.com/subzeroid/instagrapi) library. Automating Instagram breaks
its Terms of Service and **can get your account challenged, rate-limited or banned**. A
secondary account is safer. Accounts with 2FA or a security checkpoint may need you to log
in once on your phone first. Jarvis asks you before every post or DM.

## Safety

- Shell commands, running code, installing packages, sending email, Instagram posts and DMs,
  and overwriting files all show a **Yes/No confirmation** first. You can turn this off with `CONFIRM_ACTIONS=false`, but
  that isn't recommended: the AI can misunderstand.
- Your keys and passwords live only in `.env`, which is git-ignored. Never commit it.
- If a key has ever been pasted somewhere public, rotate it in Google AI Studio.

## AI providers and models

Jarvis works with **Gemini** (free key) and **ChatGPT** (OpenAI key with API credit). You can
set up either or both. Switching keeps the conversation, so the other AI knows what was said.

With `GEMINI_MODEL=auto` / `OPENAI_MODEL=auto` (the defaults), Jarvis asks each provider which
models your key can use and tries the fastest, most reliable ones first (`PREFERRED` in
`jarvis/gemini_provider.py` and `jarvis/openai_provider.py`). When a model is overloaded,
rate-limited, retired or slow, Jarvis:

- moves on to the next model right away and parks the failed one for a minute or two;
- starts a backup model if the current one hasn't answered within `GEMINI_HEDGE_SECONDS`
  (default 8), and uses whichever answers first;
- runs each tool exactly once, so a model switch never repeats a click, an email or a post.

The HUD log shows each switch. If your OpenAI account has no credit, Jarvis says so; switch
back to Gemini.

## Project layout

```
run.py                 entry point
jarvis/gui.py          full-screen HUD (customtkinter)
jarvis/app.py          input queue, speech, wake-word loop
jarvis/brain.py        persona, routes each request to the active AI, runs tools
jarvis/ai.py           active provider switch, vision and web-search helpers
jarvis/gemini_provider.py / openai_provider.py   the two AI back ends
jarvis/model_pool.py   automatic model switching (fallback + backup requests)
jarvis/voice.py        edge-tts voice output, SpeechRecognition mic input
jarvis/camera.py       webcam + sensors (face tracking, distance, motion, light) and HUD overlay
jarvis/bridge.py       thread-safe worker → GUI messages (logs, map, confirmations)
jarvis/tools/          everything Jarvis can do: system, code, web/map, vision, email, instagram, memory
```

To add a skill, write a function with type hints and a docstring in `jarvis/tools/`,
decorate it with `@tool`, and add it to that module's `TOOLS` list. Gemini picks it up
automatically.
