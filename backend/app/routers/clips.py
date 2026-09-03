"""
Phase 4: endpoints to kick off and check on AI clip selection.

Same background-task-and-poll pattern as process.py and transcribe.py.
"""
import logging
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

from .. import storage
from ..clip_selection import ClipSelectionError, select_clips

router = APIRouter(prefix="/api", tags=["clips"])
logger = logging.getLogger("reelmaker.clips")


class ClipRequest(BaseModel):
    num_clips: Literal[3, 5, 10] = 5
    target_length_seconds: Literal[30, 45, 60] = 45
    content_preference: Literal[
        "best", "educational", "funny", "storytelling", "controversial", "emotional"
    ] = "best"


def _run_clip_selection(job_id: str, req: ClipRequest) -> None:
    job = storage.load_job(job_id)
    if job is None:
        logger.error("Job %s vanished before clip selection could start.", job_id)
        return

    job["clips_status"] = "running"
    job["clips_error"] = None
    storage.save_job(job)

    transcript = storage.load_transcript(job_id)
    if transcript is None:
        job = storage.load_job(job_id)
        job["clips_status"] = "error"
        job["clips_error"] = "No transcript found - transcribe the video first."
        storage.save_job(job)
        return

    try:
        clips = select_clips(
            transcript,
            num_clips=req.num_clips,
            target_length_seconds=req.target_length_seconds,
            content_preference=req.content_preference,
        )
        storage.save_clips(job_id, clips)

        job = storage.load_job(job_id)
        job["clips_status"] = "done"
        job["clips_error"] = None
        job["clips_count"] = len(clips)
        storage.save_job(job)
    except ClipSelectionError as e:
        logger.warning("Clip selection failed for job %s: %s", job_id, e)
        job = storage.load_job(job_id)
        job["clips_status"] = "error"
        job["clips_error"] = str(e)
        storage.save_job(job)
    except Exception as e:  # noqa: BLE001 - surface any failure to the UI
        logger.exception("Clip selection failed unexpectedly for job %s", job_id)
        job = storage.load_job(job_id)
        job["clips_status"] = "error"
        job["clips_error"] = f"Unexpected error: {e}"
        storage.save_job(job)


@router.post("/jobs/{job_id}/select-clips")
async def start_clip_selection(job_id: str, req: ClipRequest, background_tasks: BackgroundTasks):
    job = storage.load_job(job_id)
    if job is None:
        raise HTTPException(404, "Job not found.")

    transcript = storage.load_transcript(job_id)
    if transcript is None:
        raise HTTPException(400, "Transcribe the video before selecting clips.")

    job["clips_status"] = "pending"
    storage.save_job(job)

    background_tasks.add_task(_run_clip_selection, job_id, req)
    return {"job": job}


@router.get("/jobs/{job_id}/clips")
async def get_clips(job_id: str):
    clips = storage.load_clips(job_id)
    if clips is None:
        raise HTTPException(404, "Clips not available yet.")
    return {"clips": clips}
