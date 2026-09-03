"""
Phase 3: endpoints to kick off and check on transcription.

Mirrors the process.py pattern from Phase 2 (background task + a status
field on the job that the frontend polls), since transcribing a 1-2 hour
video is slow enough that it must not block the HTTP request.
"""
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException

from .. import storage
from ..transcribe import transcribe_audio

router = APIRouter(prefix="/api", tags=["transcribe"])
logger = logging.getLogger("reelmaker.transcribe_router")


def _run_transcription(job_id: str) -> None:
    job = storage.load_job(job_id)
    if job is None:
        logger.error("Job %s vanished before transcription could start.", job_id)
        return

    job["transcript_status"] = "running"
    job["transcript_progress"] = 0.0
    job["transcript_error"] = None
    storage.save_job(job)

    audio_path = storage.job_dir(job_id) / "audio.wav"

    def on_progress(pct: float) -> None:
        # Reload + save just the progress field so we don't clobber other
        # concurrent updates, and so the frontend can poll it.
        j = storage.load_job(job_id)
        if j is not None:
            j["transcript_progress"] = pct
            storage.save_job(j)

    try:
        try:
            transcript = transcribe_audio(audio_path, on_progress=on_progress)
        except Exception as model_err:
            # The most common first-run failure is a blocked/unreachable connection
            # to Hugging Face while downloading model weights. Give a plain-language
            # explanation instead of a raw network stack trace.
            msg = str(model_err)
            if any(k in msg for k in ("Forbidden", "huggingface", "ProxyError", "ConnectionError", "getaddrinfo", "Name or service")):
                raise RuntimeError(
                    "Could not download the speech-to-text model. This only needs to "
                    "happen once, but it requires an internet connection that can reach "
                    "huggingface.co. If you're on a restricted network (school/office "
                    "Wi-Fi, VPN, firewall), try a different network, or ask your network "
                    "admin to allow huggingface.co."
                ) from model_err
            raise
        storage.save_transcript(job_id, transcript)

        job = storage.load_job(job_id)
        job["transcript_status"] = "done"
        job["transcript_progress"] = 100.0
        job["transcript_error"] = None
        job["transcript_summary"] = {
            "language": transcript["language"],
            "duration_seconds": transcript["duration_seconds"],
            "segment_count": len(transcript["segments"]),
            "word_count": len(transcript["words"]),
        }
        storage.save_job(job)
    except Exception as e:  # noqa: BLE001 - surface any failure to the UI
        logger.exception("Transcription failed for job %s", job_id)
        job = storage.load_job(job_id)
        if job is not None:
            job["transcript_status"] = "error"
            job["transcript_error"] = str(e)
            storage.save_job(job)


@router.post("/jobs/{job_id}/transcribe")
async def start_transcription(job_id: str, background_tasks: BackgroundTasks):
    job = storage.load_job(job_id)
    if job is None:
        raise HTTPException(404, "Job not found.")

    audio_path = storage.job_dir(job_id) / "audio.wav"
    if not audio_path.exists():
        raise HTTPException(
            400, "Audio hasn't been extracted yet (FFmpeg processing not finished)."
        )

    job["transcript_status"] = "pending"
    storage.save_job(job)

    background_tasks.add_task(_run_transcription, job_id)
    return {"job": job}


@router.get("/jobs/{job_id}/transcript")
async def get_transcript(job_id: str):
    transcript = storage.load_transcript(job_id)
    if transcript is None:
        raise HTTPException(404, "Transcript not available yet.")
    return {"transcript": transcript}
