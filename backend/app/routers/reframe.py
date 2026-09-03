"""
Phase 6: endpoints for choosing how a clip gets framed into 9:16, and for
rendering that choice into the final vertical output.

Operates on a cut clip (Phase 5's clips/clip_N.mp4) rather than the full
source video, since that's already a short, self-contained file - easy to
preview in the browser and to re-render quickly when the user tweaks a box.
"""
import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Body, HTTPException
from fastapi.responses import FileResponse

from .. import storage
from ..ffmpeg_utils import FFmpegError, probe
from ..reframe import ReframeError, _validate_positions, default_frame_plan, render_reel

router = APIRouter(prefix="/api", tags=["reframe"])
logger = logging.getLogger("reelmaker.reframe")


def _clip_and_path(job_id: str, index: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    clips = storage.load_clips(job_id)
    if not clips or index < 0 or index >= len(clips):
        raise HTTPException(404, "Clip not found.")
    clip = clips[index]
    if clip.get("cut_status") != "done":
        raise HTTPException(400, "Cut this clip (Phase 5) before framing it.")
    path = storage.clip_output_path(job_id, index)
    if not path.exists():
        raise HTTPException(404, "Cut clip file missing on disk.")
    return clips, clip


@router.get("/jobs/{job_id}/clips/{index}/frame-plan")
async def get_frame_plan(job_id: str, index: int):
    _clip_and_path(job_id, index)

    clip_path = storage.clip_output_path(job_id, index)
    try:
        info = probe(clip_path)
    except FFmpegError as e:
        raise HTTPException(500, f"Could not read clip: {e}") from e

    # Always include the source clip's own resolution: a box's normalized
    # x/y/width/height only maps to a real aspect ratio once you know the
    # frame it's normalized against (a 0.3 x 1.0 box means something very
    # different on a 16:9 clip than on a square one) - the editor needs
    # this to keep a resized box locked to the canvas's aspect ratio.
    dims = {"source_width": info["width"], "source_height": info["height"]}

    plan = storage.load_frame_plan(job_id, index)
    if plan is not None:
        return {"frame_plan": plan, "is_default": False, **dims}

    plan = default_frame_plan(info["width"], info["height"], info["duration_seconds"])
    return {"frame_plan": plan, "is_default": True, **dims}


def _run_reframe(job_id: str, index: int, frame_plan: dict[str, Any]) -> None:
    clips = storage.load_clips(job_id)
    if not clips or index >= len(clips):
        logger.error("Clip %s/%s vanished before reframing could start.", job_id, index)
        return

    clips[index]["reframe_status"] = "running"
    clips[index]["reframe_error"] = None
    storage.save_clips(job_id, clips)

    clip_path = storage.clip_output_path(job_id, index)
    out_path = storage.reel_output_path(job_id, index)

    try:
        render_reel(clip_path, frame_plan, out_path)
        clips = storage.load_clips(job_id)
        clips[index]["reframe_status"] = "done"
        clips[index]["reframe_error"] = None
        clips[index]["reel_filename"] = out_path.name
        storage.save_clips(job_id, clips)
    except ReframeError as e:
        logger.warning("Reframe validation failed for %s/%s: %s", job_id, index, e)
        clips = storage.load_clips(job_id)
        clips[index]["reframe_status"] = "error"
        clips[index]["reframe_error"] = str(e)
        storage.save_clips(job_id, clips)
    except FFmpegError as e:
        logger.exception("Reframe render failed for %s/%s", job_id, index)
        clips = storage.load_clips(job_id)
        clips[index]["reframe_status"] = "error"
        clips[index]["reframe_error"] = str(e)
        storage.save_clips(job_id, clips)
    except Exception as e:  # noqa: BLE001 - surface any failure to the UI
        logger.exception("Reframe failed unexpectedly for %s/%s", job_id, index)
        clips = storage.load_clips(job_id)
        clips[index]["reframe_status"] = "error"
        clips[index]["reframe_error"] = f"Unexpected error: {e}"
        storage.save_clips(job_id, clips)


@router.post("/jobs/{job_id}/clips/{index}/reframe")
async def start_reframe(
    job_id: str, index: int, background_tasks: BackgroundTasks, body: dict[str, Any] = Body(...)
):
    clips, clip = _clip_and_path(job_id, index)

    frame_plan = {
        "canvas": body.get("canvas") or {"width": 1080, "height": 1920},
        "positions": body.get("positions"),
    }
    if not frame_plan["positions"]:
        raise HTTPException(400, "frame_plan.positions is required.")

    # Validate synchronously (cheap, no ffmpeg) so bad input fails fast with
    # a clear error rather than surfacing only after a background task runs.
    clip_path = storage.clip_output_path(job_id, index)
    try:
        info = probe(clip_path)
    except FFmpegError as e:
        raise HTTPException(500, f"Could not read clip: {e}") from e

    try:
        _validate_positions(frame_plan["positions"], info["duration_seconds"])
    except ReframeError as e:
        raise HTTPException(400, str(e)) from e

    storage.save_frame_plan(job_id, index, frame_plan)

    clips[index]["reframe_status"] = "pending"
    clips[index]["reframe_error"] = None
    # The reel this frame plan produces is about to change - any style/
    # caption result already rendered was made from the OLD crop, so it's
    # stale. Without this, best_reel_input_path() keeps serving that old
    # styled/captioned file (still marked "done") instead of anything
    # reflecting the new frame selection.
    storage.invalidate_downstream_renders(job_id, clips[index], index)
    storage.save_clips(job_id, clips)

    background_tasks.add_task(_run_reframe, job_id, index, frame_plan)
    return {"clip": clips[index]}


@router.get("/jobs/{job_id}/clips/{index}/reel")
async def download_reel(job_id: str, index: int):
    clips = storage.load_clips(job_id)
    if not clips or index < 0 or index >= len(clips):
        raise HTTPException(404, "Clip not found.")

    clip = clips[index]
    if clip.get("reframe_status") != "done":
        raise HTTPException(404, "This clip hasn't been reframed to 9:16 yet.")

    path = storage.reel_output_path(job_id, index)
    if not path.exists():
        raise HTTPException(404, "Reel file missing on disk.")

    title = clip.get("title") or f"reel_{index + 1}"
    safe_title = "".join(c for c in title if c.isalnum() or c in " -_").strip() or f"reel_{index + 1}"
    # inline, same reasoning as the raw clip file endpoint - lets a future
    # preview play this in-browser; the frontend forces an actual download
    # with the `download` attribute on its link regardless of this header.
    return FileResponse(
        path, media_type="video/mp4", filename=f"{safe_title}_9x16.mp4", content_disposition_type="inline"
    )
