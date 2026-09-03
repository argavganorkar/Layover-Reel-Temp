"""
Phase 2: prove a video can actually be processed with FFmpeg.

This adds a background step that runs right after upload: probe the file
for duration/resolution/fps, and extract its audio track. Both are quick
operations (no re-encoding of video), and audio.wav is exactly what
Phase 3's transcription step will consume, so this isn't throwaway work.
"""
import logging
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException

from .. import storage
from ..ffmpeg_utils import FFmpegError, extract_audio, probe

router = APIRouter(prefix="/api", tags=["process"])
logger = logging.getLogger("reelmaker.process")


def _run_probe_and_extract(job_id: str) -> None:
    job = storage.load_job(job_id)
    if job is None:
        logger.error("Job %s vanished before probing could start.", job_id)
        return

    job["probe_status"] = "running"
    job["probe_error"] = None
    storage.save_job(job)

    video_path = storage.job_dir(job_id) / job["stored_filename"]
    audio_path = storage.job_dir(job_id) / "audio.wav"

    try:
        info = probe(video_path)
        extract_audio(video_path, audio_path)

        job = storage.load_job(job_id)  # reload in case anything else touched it
        job["media_info"] = info
        job["audio_extracted"] = True
        job["probe_status"] = "done"
        job["probe_error"] = None
        storage.save_job(job)
    except FFmpegError as e:
        job = storage.load_job(job_id)
        job["probe_status"] = "error"
        job["probe_error"] = str(e)
        storage.save_job(job)
        logger.exception("FFmpeg processing failed for job %s", job_id)


@router.post("/jobs/{job_id}/process")
async def start_processing(job_id: str, background_tasks: BackgroundTasks):
    job = storage.load_job(job_id)
    if job is None:
        raise HTTPException(404, "Job not found.")

    video_path = storage.job_dir(job_id) / job["stored_filename"]
    if not video_path.exists():
        raise HTTPException(404, "Source video file is missing on disk.")

    job["probe_status"] = "pending"
    storage.save_job(job)

    background_tasks.add_task(_run_probe_and_extract, job_id)
    return {"job": job}
