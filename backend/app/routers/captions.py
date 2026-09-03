"""
Phase 8: endpoints for generating, editing, and rendering a clip's
"experimental semantic typography" caption plan.

Three separate concerns, three separate endpoints, so the frontend can do
each independently:
  - generating a plan (slow: one LLM call)
  - saving an edited plan (fast: just a file write - Phase 8c's light edits)
  - rendering the currently-saved plan onto the reel (slow: a real headless
    browser + ffmpeg pass, so it runs as a background task like reframing)

Operates on top of an already-reframed clip (Phase 6's 9:16 reel), since
that's the base video captions get burned onto.
"""
import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Body, HTTPException
from fastapi.responses import FileResponse

from .. import storage
from ..caption_render import CaptionRenderError, build_caption_html, render_captioned_reel
from ..captions import CaptionError, generate_caption_plan

router = APIRouter(prefix="/api", tags=["captions"])
logger = logging.getLogger("reelmaker.captions")

DEFAULT_CANVAS = {"width": 1080, "height": 1920}


def _clip_and_reel_path(job_id: str, index: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    clips = storage.load_clips(job_id)
    if not clips or index < 0 or index >= len(clips):
        raise HTTPException(404, "Clip not found.")
    clip = clips[index]
    if clip.get("reframe_status") != "done":
        raise HTTPException(400, "Frame this clip to 9:16 (Phase 6) before captioning it.")
    path = storage.best_reel_input_path(job_id, index, clip)
    if not path.exists():
        raise HTTPException(404, "Reframed reel file missing on disk.")
    return clips, clip


def _clip_relative_words(job_id: str, clip: dict[str, Any]) -> list[dict[str, Any]]:
    """
    The saved transcript's words are timestamped against the full source
    video. A clip's own `start`/`end` (kept current by both the original
    cut and any later trim - see routers/cut.py) mark out its slice of
    that timeline. Re-zeroes every word's start/end to be clip-relative,
    which is what generate_caption_plan expects.
    """
    transcript = storage.load_transcript(job_id)
    if not transcript or not transcript.get("words"):
        raise HTTPException(400, "No transcript available for this job.")

    clip_start = float(clip["start"])
    clip_end = float(clip["end"])
    out: list[dict[str, Any]] = []
    for w in transcript["words"]:
        # A word counts as "in" the clip if it overlaps the clip's span at
        # all (not just fully contained) - otherwise a word straddling the
        # cut boundary silently vanishes from the caption source text.
        if w["end"] <= clip_start or w["start"] >= clip_end:
            continue
        out.append(
            {
                **w,
                "start": round(max(0.0, w["start"] - clip_start), 2),
                "end": round(min(clip_end - clip_start, w["end"] - clip_start), 2),
            }
        )
    return out


@router.get("/jobs/{job_id}/clips/{index}/caption-plan")
async def get_caption_plan(job_id: str, index: int):
    _clip_and_reel_path(job_id, index)
    beats = storage.load_caption_plan(job_id, index)
    return {"beats": beats}


@router.post("/jobs/{job_id}/clips/{index}/caption-plan/generate")
async def generate_plan(job_id: str, index: int):
    """Runs the LLM once to produce a fresh caption plan and saves it as the clip's current plan."""
    clips, clip = _clip_and_reel_path(job_id, index)
    words = _clip_relative_words(job_id, clip)

    try:
        beats = generate_caption_plan(words)
    except CaptionError as e:
        raise HTTPException(400, str(e)) from e

    storage.save_caption_plan(job_id, index, beats)
    return {"beats": beats}


@router.put("/jobs/{job_id}/clips/{index}/caption-plan")
async def save_plan(job_id: str, index: int, body: dict[str, Any] = Body(...)):
    """Saves a user-edited plan (Phase 8c's light edits) without touching the LLM."""
    _clip_and_reel_path(job_id, index)
    beats = body.get("beats")
    if not isinstance(beats, list) or not beats:
        raise HTTPException(400, "beats must be a non-empty list.")
    storage.save_caption_plan(job_id, index, beats)
    return {"beats": beats}


@router.post("/jobs/{job_id}/clips/{index}/caption-plan/preview-html")
async def preview_html(job_id: str, index: int, body: dict[str, Any] = Body(...)):
    """
    Builds the same self-contained caption-stage HTML the offline Playwright
    export uses (see caption_render.build_caption_html) for a given set of
    beats, WITHOUT saving anything - lets the frontend live-preview edits
    (including unsaved ones) with the exact styling engine that will burn
    them in, rather than a second reimplementation in React/CSS.
    """
    _clip_and_reel_path(job_id, index)
    beats = body.get("beats")
    if not isinstance(beats, list) or not beats:
        raise HTTPException(400, "beats must be a non-empty list.")
    html = build_caption_html(beats, DEFAULT_CANVAS)
    return {"html": html, "canvas": DEFAULT_CANVAS}


def _run_caption_render(job_id: str, index: int, beats: list[dict[str, Any]]) -> None:
    clips = storage.load_clips(job_id)
    if not clips or index >= len(clips):
        logger.error("Clip %s/%s vanished before caption rendering could start.", job_id, index)
        return

    clips[index]["caption_status"] = "running"
    clips[index]["caption_error"] = None
    storage.save_clips(job_id, clips)

    reel_path = storage.best_reel_input_path(job_id, index, clips[index])
    out_path = storage.captioned_reel_output_path(job_id, index)

    try:
        render_captioned_reel(reel_path, beats, DEFAULT_CANVAS, out_path)
        clips = storage.load_clips(job_id)
        clips[index]["caption_status"] = "done"
        clips[index]["caption_error"] = None
        clips[index]["captioned_reel_filename"] = out_path.name
        storage.save_clips(job_id, clips)
    except CaptionRenderError as e:
        logger.warning("Caption render failed for %s/%s: %s", job_id, index, e)
        clips = storage.load_clips(job_id)
        clips[index]["caption_status"] = "error"
        clips[index]["caption_error"] = str(e)
        storage.save_clips(job_id, clips)
    except Exception as e:  # noqa: BLE001 - surface any failure to the UI
        logger.exception("Caption render failed unexpectedly for %s/%s", job_id, index)
        clips = storage.load_clips(job_id)
        clips[index]["caption_status"] = "error"
        clips[index]["caption_error"] = f"Unexpected error: {e}"
        storage.save_clips(job_id, clips)


@router.post("/jobs/{job_id}/clips/{index}/captions/render")
async def start_caption_render(job_id: str, index: int, background_tasks: BackgroundTasks):
    clips, clip = _clip_and_reel_path(job_id, index)
    beats = storage.load_caption_plan(job_id, index)
    if not beats:
        raise HTTPException(400, "Generate or save a caption plan before rendering.")

    clips[index]["caption_status"] = "pending"
    clips[index]["caption_error"] = None
    # If an outro was already added, it was baked onto whatever was most-
    # finished before this caption render - stale now that the captioned
    # reel is about to be (re)created underneath it.
    storage.invalidate_outro(job_id, clips[index], index)
    storage.save_clips(job_id, clips)

    background_tasks.add_task(_run_caption_render, job_id, index, beats)
    return {"clip": clips[index]}


@router.get("/jobs/{job_id}/clips/{index}/captioned-reel")
async def download_captioned_reel(job_id: str, index: int):
    clips = storage.load_clips(job_id)
    if not clips or index < 0 or index >= len(clips):
        raise HTTPException(404, "Clip not found.")

    clip = clips[index]
    if clip.get("caption_status") != "done":
        raise HTTPException(404, "This clip hasn't had captions rendered yet.")

    path = storage.captioned_reel_output_path(job_id, index)
    if not path.exists():
        raise HTTPException(404, "Captioned reel file missing on disk.")

    title = clip.get("title") or f"reel_{index + 1}"
    safe_title = "".join(c for c in title if c.isalnum() or c in " -_").strip() or f"reel_{index + 1}"
    return FileResponse(
        path,
        media_type="video/mp4",
        filename=f"{safe_title}_captioned.mp4",
        content_disposition_type="inline",
    )
