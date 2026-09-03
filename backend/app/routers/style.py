"""
Phase 10: endpoint for applying the reference-style "visual DNA" preset to a
clip - one call, background task, final styled reel out. Mirrors the
reframe/captions router pattern (pending -> running -> done/error on the
clip record, background_tasks for the slow work).

Operates on top of an already-reframed clip (Phase 6's 9:16 reel), same
input as captions. If a caption plan already exists for this clip, applying
(or re-applying) the style does NOT touch it - `storage.best_reel_input_path`
means captions just render onto the styled reel from then on, so the two
stages compose in either order.
"""
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse

from .. import storage
from ..style_dna import StyleDNAError, render_style_dna_video

router = APIRouter(prefix="/api", tags=["style"])
logger = logging.getLogger("reelmaker.style")


def _clip_and_reel_path(job_id: str, index: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    clips = storage.load_clips(job_id)
    if not clips or index < 0 or index >= len(clips):
        raise HTTPException(404, "Clip not found.")
    clip = clips[index]
    if clip.get("reframe_status") != "done":
        raise HTTPException(400, "Frame this clip to 9:16 (Phase 6) before styling it.")
    path = storage.reel_output_path(job_id, index)
    if not path.exists():
        raise HTTPException(404, "Reframed reel file missing on disk.")
    return clips, clip


def _run_style_render(job_id: str, index: int) -> None:
    clips = storage.load_clips(job_id)
    if not clips or index >= len(clips):
        logger.error("Clip %s/%s vanished before style rendering could start.", job_id, index)
        return

    clips[index]["style_status"] = "running"
    clips[index]["style_error"] = None
    clips[index]["style_started_at"] = datetime.now(timezone.utc).isoformat()
    clips[index]["style_frames_done"] = 0
    clips[index]["style_frames_total"] = None
    storage.save_clips(job_id, clips)

    reel_path = storage.reel_output_path(job_id, index)
    out_path = storage.styled_reel_output_path(job_id, index)

    def _on_progress(frames_done: int, frames_total: int) -> None:
        # Called roughly once a second by render_style_dna_video, not once
        # per frame - a render this slow could run thousands of frames, and
        # this does a full read-modify-write of clips.json each time, so
        # per-frame would be needless disk churn for no visible UI benefit.
        current = storage.load_clips(job_id)
        if not current or index >= len(current):
            return
        current[index]["style_frames_done"] = frames_done
        current[index]["style_frames_total"] = frames_total
        storage.save_clips(job_id, current)

    try:
        render_style_dna_video(reel_path, out_path, progress_cb=_on_progress)
        clips = storage.load_clips(job_id)
        clips[index]["style_status"] = "done"
        clips[index]["style_error"] = None
        clips[index]["styled_reel_filename"] = out_path.name
        storage.save_clips(job_id, clips)
    except StyleDNAError as e:
        logger.warning("Style render failed for %s/%s: %s", job_id, index, e)
        clips = storage.load_clips(job_id)
        clips[index]["style_status"] = "error"
        clips[index]["style_error"] = str(e)
        storage.save_clips(job_id, clips)
    except Exception as e:  # noqa: BLE001 - surface any failure to the UI
        logger.exception("Style render failed unexpectedly for %s/%s", job_id, index)
        clips = storage.load_clips(job_id)
        clips[index]["style_status"] = "error"
        clips[index]["style_error"] = f"Unexpected error: {e}"
        storage.save_clips(job_id, clips)


@router.post("/jobs/{job_id}/clips/{index}/style/render")
async def start_style_render(job_id: str, index: int, background_tasks: BackgroundTasks):
    clips, clip = _clip_and_reel_path(job_id, index)

    clips[index]["style_status"] = "pending"
    clips[index]["style_error"] = None
    # If an outro was already added, it was baked onto whatever was most-
    # finished before this style applied/re-applied - stale now that the
    # style is about to change underneath it.
    storage.invalidate_outro(job_id, clips[index], index)
    storage.save_clips(job_id, clips)

    background_tasks.add_task(_run_style_render, job_id, index)
    return {"clip": clips[index]}


@router.delete("/jobs/{job_id}/clips/{index}/style")
async def clear_style(job_id: str, index: int):
    """Reverts a clip back to the plain (unstyled) reel - clears the status
    so captions once again render onto the reframed reel directly. Doesn't
    delete the styled file itself, just stops using it, so re-applying is a
    cheap status flip if the file's still there... but for a clean state we
    remove it, since a stale styled_reel_filename with no status would be a
    confusing thing to leave lying around on disk."""
    clips = storage.load_clips(job_id)
    if not clips or index < 0 or index >= len(clips):
        raise HTTPException(404, "Clip not found.")

    styled_path = storage.styled_reel_output_path(job_id, index)
    styled_path.unlink(missing_ok=True)

    clips[index]["style_status"] = None
    clips[index]["style_error"] = None
    clips[index]["styled_reel_filename"] = None
    # Same reasoning as start_style_render above - removing the style
    # changes what "most finished" means, so any outro baked on top of the
    # (now-removed) styled reel no longer reflects the clip's actual state.
    storage.invalidate_outro(job_id, clips[index], index)
    storage.save_clips(job_id, clips)
    return {"clip": clips[index]}


@router.get("/jobs/{job_id}/clips/{index}/styled-reel")
async def download_styled_reel(job_id: str, index: int):
    clips = storage.load_clips(job_id)
    if not clips or index < 0 or index >= len(clips):
        raise HTTPException(404, "Clip not found.")

    clip = clips[index]
    if clip.get("style_status") != "done":
        raise HTTPException(404, "This clip hasn't had the style applied yet.")

    path = storage.styled_reel_output_path(job_id, index)
    if not path.exists():
        raise HTTPException(404, "Styled reel file missing on disk.")

    title = clip.get("title") or f"reel_{index + 1}"
    safe_title = "".join(c for c in title if c.isalnum() or c in " -_").strip() or f"reel_{index + 1}"
    return FileResponse(
        path, media_type="video/mp4", filename=f"{safe_title}_styled.mp4", content_disposition_type="inline"
    )
