"""
Phase 1: accept a long-form video upload and store it on disk.

Later phases add endpoints in their own router files (process.py, jobs.py, etc.)
and mount them in main.py, so this file stays focused on "get the video onto disk".
"""
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from .. import storage
from ..config import ALLOWED_VIDEO_EXTENSIONS, MAX_UPLOAD_BYTES
from .process import _run_probe_and_extract

router = APIRouter(prefix="/api", tags=["upload"])

# Stream to disk in chunks so we never hold a 1-2 hour video fully in memory.
CHUNK_SIZE = 1024 * 1024  # 1 MB


@router.post("/upload")
async def upload_video(file: UploadFile, background_tasks: BackgroundTasks):
    if not file.filename:
        raise HTTPException(400, "No filename provided.")

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_VIDEO_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_VIDEO_EXTENSIONS))
        raise HTTPException(400, f"Unsupported file type '{ext}'. Allowed: {allowed}")

    # Create the job first so we know where to stream the file to.
    stored_filename = f"source{ext}"
    job = storage.create_job(
        original_filename=file.filename,
        size_bytes=0,  # filled in once we know the real size
        stored_filename=stored_filename,
    )
    dest_path = storage.job_dir(job["id"]) / stored_filename

    size_bytes = 0
    try:
        with open(dest_path, "wb") as out:
            while chunk := await file.read(CHUNK_SIZE):
                size_bytes += len(chunk)
                if size_bytes > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "File too large.")
                out.write(chunk)
    except HTTPException:
        # Clean up a partial job/file on failure.
        dest_path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

    job["size_bytes"] = size_bytes
    job["probe_status"] = "pending"
    storage.save_job(job)

    # Kick off ffprobe + audio extraction right away, in the background,
    # so the upload response comes back immediately.
    background_tasks.add_task(_run_probe_and_extract, job["id"])

    return JSONResponse({"job": job}, status_code=201)


@router.get("/jobs")
async def get_jobs():
    return {"jobs": storage.list_jobs()}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str):
    job = storage.load_job(job_id)
    if job is None:
        raise HTTPException(404, "Job not found.")
    return {"job": job}


@router.delete("/jobs/{job_id}")
async def delete_job(job_id: str):
    """Deletes an upload and everything derived from it (clips, reels, captions). Irreversible - the frontend confirms with the user first."""
    deleted = storage.delete_job(job_id)
    if not deleted:
        raise HTTPException(404, "Job not found.")
    return {"deleted": True}
