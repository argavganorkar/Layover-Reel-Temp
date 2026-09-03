# Reel Maker

A personal tool: upload a long video, AI finds the best moments, and you get
short vertical (9:16) reels back out with burned-in captions, custom framing,
a reference visual style, and an optional branded outro.

No accounts, no database, no cloud hosting, nothing shared between users —
everything runs entirely on your own machine and all your files stay on your
own disk. There's no "the app" living on a server somewhere; this folder
**is** the app. Anyone who wants to use it runs their own copy, with their
own uploads folder, completely independent of anyone else's.

## What's built

- Upload a video, auto-transcribe it (local, free, runs on your own machine).
- AI picks the best short moments and explains why (your choice of a local
  free model via Ollama, or a paid API: Anthropic/OpenAI/Gemini).
- Trim/adjust each clip's start and end.
- Reframe to 9:16 with several layouts (Vertical, Free, Centered, Spotlight,
  Split, Trio, Horizontal) — pick exactly what stays in frame.
- Apply a reference "visual DNA" style: a clean black-and-white, paper-grain
  cutout look with temporally-stable subject tracking.
- Auto-generate captions in a fixed three-tier style (setup / punch / accent),
  with per-beat color, opacity, and position overrides available.
- Append a branded outro clip to the end of any reel.
- A "Your reels" gallery and a full history of every upload, with one-click
  downloads for whichever version of a clip is most finished.

## Prerequisites (one-time, per machine)

Whoever runs this needs, on their own computer:

1. **Python 3.11+** — https://www.python.org/downloads/
2. **Node.js 20.9+** — https://nodejs.org (LTS is fine)
3. **FFmpeg** — must be on your system PATH (`ffmpeg -version` should work
   from any terminal). Windows: https://www.gyan.dev/ffmpeg/builds/ (grab a
   "release full" build, add its `bin` folder to PATH). Mac: `brew install
   ffmpeg`. Linux: your package manager (`apt install ffmpeg`, etc.).
4. **Git** (optional, only needed if you're getting the code via a Git repo
   rather than a zip file).
5. **A way to run the AI clip-selection step** — pick one:
   - **Ollama (free, runs locally, default)** — install from
     https://ollama.com, then run `ollama pull llama3.1:8b` once. No API key,
     no cost, but needs a reasonably capable machine (8GB+ RAM free).
   - **A paid/free-tier API key instead** — Anthropic, OpenAI, or Google
     Gemini (Gemini has a genuinely free tier). No local model needed, just
     an API key. Faster and less demanding on your machine than Ollama.

GPU acceleration for the style-render step (optional, Windows only right
now): if you're on Windows with a dedicated GPU (NVIDIA, AMD, or Intel Arc),
the app will automatically use it via DirectML once you install
`onnxruntime-directml` instead of plain `onnxruntime` (see below) — this can
take a style render from ~40 minutes down to a few minutes. On Mac/Linux, or
Windows without a dedicated GPU, style rendering runs on CPU and takes
longer; there's no GPU-acceleration path for Mac/Linux yet.

## One-time setup

### 1. Get the code

Unzip the project folder wherever you want it to live (e.g.
`Desktop\ReelMaker` on Windows, or `~/ReelMaker` on Mac/Linux).

### 2. Backend (Python)

Open a terminal, `cd` into the `backend` folder inside the project, then:

**Windows (PowerShell):**
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

**Mac/Linux:**
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

`requirements.txt` automatically installs the right style-render package for
your OS (`onnxruntime-directml` on Windows for GPU acceleration, plain
`onnxruntime` everywhere else) — no manual choice needed.

Now open the new `.env` file in a text editor and set `LLM_PROVIDER`:
- Leave it as `ollama` if you installed Ollama above (make sure
  `OLLAMA_MODEL` matches what you pulled).
- Otherwise set it to `anthropic`, `openai`, or `gemini` and paste your API
  key into the matching `..._API_KEY` line.

### 3. Frontend (Node)

In a **separate** terminal, `cd` into the `frontend` folder:

```bash
npm install
```

## Running it

Every time you want to use the app, start both servers and leave both
windows open while you use it:

**Window 1 — backend:**

Windows:
```powershell
cd backend
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --port 8000
```

Mac/Linux:
```bash
cd backend
source .venv/bin/activate
uvicorn app.main:app --port 8000
```

**Window 2 — frontend:**
```bash
cd frontend
npm run dev
```

Then open **http://localhost:3000** in your browser. Everything you upload
and every reel you make lives only in this project's `backend/data/` folder,
on this machine — nothing is sent anywhere except the (optional) AI API
calls for clip selection and, if you're not using Ollama, nothing local-only
at all.

## Troubleshooting

- **A banner saying "Failed to fetch. Make sure the backend server is
  running on port 8000."** — exactly what it says: the backend window isn't
  running, crashed, or you closed it. Start it again (see above).
- **Video previews show solid white / won't play** — almost always the same
  backend-not-running issue above; check that window first before anything
  else.
- **`onnxruntime` install conflict** — `onnxruntime` and
  `onnxruntime-directml` provide the same module and can't both be
  installed. If you ever need to switch (e.g. moving the project between a
  Windows machine and a Mac), `pip uninstall onnxruntime -y` (it matches
  either package name) before reinstalling from `requirements.txt`.

## Sharing this with someone else

There's no shared server — if a friend wants their own copy, give them this
whole project folder (minus `backend/.venv`, `backend/data`, `frontend/node_modules`,
and `frontend/.next` — those are machine-specific and regenerated by the
setup steps above) and have them follow "One-time setup" on their own
machine. Their uploads, reels, and API keys stay entirely on their own
computer, completely separate from yours.
