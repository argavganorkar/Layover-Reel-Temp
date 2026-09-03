"""
Central configuration for the Reel Maker backend.

This is a personal, single-user tool: no database, no auth.
All state lives on disk under DATA_DIR, one folder per job.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# Root of the backend/ package (this file's parent's parent)
BASE_DIR = Path(__file__).resolve().parent.parent

# API keys / provider choice live in backend/.env (see .env.example), not in
# code, so you never have to touch a source file to plug in a key.
load_dotenv(BASE_DIR / ".env")

# Which LLM does clip selection (Phase 4): "ollama" (local, free), "anthropic"
# (Claude), "openai", or "gemini". Swappable via env var so you can switch
# providers without touching code - see backend/.env.example.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama").lower()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5-20250929")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# Ollama runs locally - no key needed, just a model name and the local
# server's address (default is correct for a normal local install).
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

# Where uploaded videos, transcripts, clips, etc. are stored.
# Layout (built up over the phases):
#   data/uploads/<job_id>/source.<ext>   - the original uploaded video
#   data/uploads/<job_id>/job.json       - job metadata + status
#   data/uploads/<job_id>/audio.wav      - extracted audio (Phase 2)
#   data/uploads/<job_id>/transcript.json- transcript with timestamps (Phase 3)
#   data/uploads/<job_id>/clips.json     - AI-selected clip candidates (Phase 4)
#   data/uploads/<job_id>/clips/*.mp4    - rendered reels (Phase 5+)
DATA_DIR = BASE_DIR / "data"
UPLOADS_DIR = DATA_DIR / "uploads"

UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

# Video files we accept for upload.
ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}

# Generous cap for a 1-2 hour source video. Adjust if needed.
MAX_UPLOAD_BYTES = 8 * 1024 * 1024 * 1024  # 8 GB

# Frontend origin allowed to call this API (Next.js dev server).
FRONTEND_ORIGIN = "http://localhost:3000"
