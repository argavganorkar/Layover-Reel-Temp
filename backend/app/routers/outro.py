"""
Add-outro: appends the bundled outro clip (app/assets/outro.mp4) onto the
end of a clip's most-finished reel, one click. Mirrors the style/captions
router pattern (pending -> running -> done/error on the clip record,
background_tasks for the render, DELETE to revert).
"""
import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse

from .. import storage
from ..outro import OutroError, render_with_outro

router = APIRouter(prefix="/api", tags=["outro"])
logger = logging.getLogger("reelmaker.outro")

DEFAULT_CANVAS = {"width": 1080, "height": 1920}


def _clip_and_input_path(job_id: str, index: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    clips = storage.load_clips(job_id)
    if not clips or index < 0 or index >= len(clips):
        raise HTTPException(404, "Clip not found.")
    clip = clips[index]
    if clip.get("reframe_status") != "done":
        raise HTTPException(400, "Frame this clip to 9:16 (Phase 6) before adding an outro.")
    path = storage.most_finished_reel_path(job_id, index, clip)
    if not path.exists():
        raise HTTPException(404, "This clip's reel file is missing on disk.")
    return clips, clip


def _run_outro_render(job_id: str, index: int) -> None:
    clips = storage.load_clips(job_id)
    if not clips or index >= len(clips):
        logger.error("Clip %s/%s vanished before outro rendering could start.", job_id, index)
        return

    clips[index]["outro_status"] = "running"
    clips[index]["outro_error"] = None
    storage.save_clips(job_id, clips)

    main_path = storage.most_finished_reel_path(job_id, index, clips[index])
    out_path = storage.outro_reel_output_path(job_id, index)

    try:
        render_with_outro(main_path, out_path, DEFAULT_CANVAS)
        clips = storage.load_clips(job_id)
        clips[index]["outro_status"] = "done"
        clips[index]["outro_error"] = None
        clips[index]["outro_reel_filename"] = out_path.name
        storage.save_clips(job_id, clips)
    except OutroError as e:
        logger.warning("Outro render failed for %s/%s: %s", job_id, index, e)
        clips = storage.load_clips(job_id)
        clips[index]["outro_status"] = "error"
        clips[index]["outro_error"] = str(e)
        storage.save_clips(job_id, clips)
    except Exception as e:  # noqa: BLE001 - surface any failure to the UI
        logger.exception("Outro render failed unexpectedly for %s/%s", job_id, index)
        clips = storage.load_clips(job_id)
        clips[index]["outro_status"] = "error"
        clips[index]["outro_error"] = f"Unexpected error: {e}"
        storage.save_clips(job_id, clips)


@router.post("/jobs/{job_id}/clips/{index}/outro")
async def start_outro_render(job_id: str, index: int, background_tasks: BackgroundTasks):
    clips, clip = _clip_and_input_path(job_id, index)

    clips[index]["outro_status"] = "pending"
    clips[index]["outro_error"] = None
    storage.save_clips(job_id, clips)

    background_tasks.add_task(_run_outro_render, job_id, index)
    return {"clip": clips[index]}


@router.delete("/jobs/{job_id}/clips/{index}/outro")
async def clear_outro(job_id: str, index: int):
    """Removes the outro'd file and clears its status - the clip's underlying reel is untouched."""
    clips = storage.load_clips(job_id)
    if not clips or index < 0 or index >= len(clips):
        raise HTTPException(404, "Clip not found.")

    storage.invalidate_outro(job_id, clips[index], index)
    storage.save_clips(job_id, clips)
    return {"clip": clips[index]}


@router.get("/jobs/{job_id}/clips/{index}/outro-reel")
async def download_outro_reel(job_id: str, index: int):
    clips = storage.load_clips(job_id)
    if not clips or index < 0 or index >= len(clips):
        raise HTTPException(404, "Clip not found.")

    clip = clips[index]
    if clip.get("outro_status") != "done":
        raise HTTPException(404, "This clip hasn't had the outro added yet.")

    path = storage.outro_reel_output_path(job_id, index)
    if not path.exists():
        raise HTTPException(404, "Outro reel file missing on disk.")

    title = clip.get("title") or f"reel_{index + 1}"
    safe_title = "".join(c for c in title if c.isalnum() or c in " -_").strip() or f"reel_{index + 1}"
    return FileResponse(
        path, media_type="video/mp4", filename=f"{safe_title}_with_outro.mp4", content_disposition_type="inline"
    )
