"""
Personal AI Video Reel Maker - backend entrypoint.

Run with:  uvicorn app.main:app --reload --port 8000
(from inside backend/, with the virtualenv activated)
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import FRONTEND_ORIGIN
from .routers import captions, clips, cut, outro, process, reframe, style, transcribe, upload

app = FastAPI(title="Reel Maker API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(upload.router)
app.include_router(process.router)
app.include_router(transcribe.router)
app.include_router(clips.router)
app.include_router(cut.router)
app.include_router(reframe.router)
app.include_router(style.router)
app.include_router(captions.router)
app.include_router(outro.router)


@app.get("/api/health")
async def health():
    return {"status": "ok"}
