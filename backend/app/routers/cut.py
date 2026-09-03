"""
Phase 5: cut the AI-selected clips out of the source video with FFmpeg.

Takes the clips.json produced by Phase 4 (start/end/title/hook/reason/score
- no actual video yet) and, for each one, cuts a real .mp4 file out of the
original source video at that time range. This is a straight cut at the
source's original resolution/aspect ratio; reframing to 9:16 is Phase 6,
captions are Phase 8.

Same background-task-and-poll pattern as the earlier phases, plus a
per-clip cut_status/output_filename recorded directly on each entry in
clips.json so the frontend can show progress and offer a download link as
soon as each individual clip finishes (cutting N clips can take a while on
a long video, no reason to make the user wait for all of them).
"""
import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Body, HTTPException
from fastapi.responses import FileResponse

from .. import storage
from ..ffmpeg_utils import FFmpegError, cut_clip

router = APIRouter(prefix="/api", tags=["cut"])
logger = logging.getLogger("reelmaker.cut")

MIN_CLIP_SECONDS = 1.0


def _run_cut_clips(job_id: str) -> None:
    job = storage.load_job(job_id)
    if job is None:
        logger.error("Job %s vanished before cutting could start.", job_id)
        return

    clips = storage.load_clips(job_id)
    if not clips:
        job["cut_status"] = "error"
        job["cut_error"] = "No clips found - run clip selection first."
        storage.save_job(job)
        return

    job["cut_status"] = "running"
    job["cut_error"] = None
    storage.save_job(job)

    source_path = storage.job_dir(job_id) / job["stored_filename"]
    has_audio = bool((job.get("media_info") or {}).get("has_audio", True))
    storage.clips_dir(job_id).mkdir(parents=True, exist_ok=True)

    for i, clip in enumerate(clips):
        clip["cut_status"] = "running"
        clip["cut_error"] = None
        storage.save_clips(job_id, clips)
        try:
            out_path = storage.clip_output_path(job_id, i)
            cut_clip(source_path, out_path, clip["start"], clip["end"], has_audio=has_audio)
            clip["cut_status"] = "done"
            clip["output_filename"] = out_path.name
        except FFmpegError as e:
            logger.exception("Cutting clip %s failed for job %s", i, job_id)
            clip["cut_status"] = "error"
            clip["cut_error"] = str(e)
        storage.save_clips(job_id, clips)

    failures = [c for c in clips if c.get("cut_status") == "error"]
    job = storage.load_job(job_id)
    job["cut_status"] = "error" if len(failures) == len(clips) else "done"
    job["cut_error"] = (
        f"{len(failures)} of {len(clips)} clips failed to cut." if failures else None
    )
    storage.save_job(job)


@router.post("/jobs/{job_id}/cut-clips")
async def start_cutting(job_id: str, background_tasks: BackgroundTasks):
    job = storage.load_job(job_id)
    if job is None:
        raise HTTPException(404, "Job not found.")

    clips = storage.load_clips(job_id)
    if not clips:
        raise HTTPException(400, "Select clips before cutting them.")

    source_path = storage.job_dir(job_id) / job["stored_filename"]
    if not source_path.exists():
        raise HTTPException(404, "Source video file is missing on disk.")

    job["cut_status"] = "pending"
    job["cut_error"] = None
    storage.save_job(job)

    background_tasks.add_task(_run_cut_clips, job_id)
    return {"job": job}


def _recut_one_clip(job_id: str, index: int) -> None:
    """
    Re-cuts a single clip after its start/end was adjusted (Phase 6.5 -
    manual timing tweaks). Always re-cuts from the ORIGINAL source video,
    never from the previously-cut short clip file, since "extend a couple
    seconds earlier/later" needs footage that may not exist in that short
    clip at all.
    """
    job = storage.load_job(job_id)
    if job is None:
        logger.error("Job %s vanished before re-cutting could start.", job_id)
        return
    clips = storage.load_clips(job_id)
    if not clips or index >= len(clips):
        logger.error("Clip %s/%s vanished before re-cutting could start.", job_id, index)
        return

    clips[index]["cut_status"] = "running"
    clips[index]["cut_error"] = None
    storage.save_clips(job_id, clips)

    source_path = storage.job_dir(job_id) / job["stored_filename"]
    has_audio = bool((job.get("media_info") or {}).get("has_audio", True))
    storage.clips_dir(job_id).mkdir(parents=True, exist_ok=True)

    try:
        out_path = storage.clip_output_path(job_id, index)
        clip = clips[index]
        cut_clip(source_path, out_path, clip["start"], clip["end"], has_audio=has_audio)
        clips = storage.load_clips(job_id)
        clips[index]["cut_status"] = "done"
        clips[index]["cut_error"] = None
        clips[index]["output_filename"] = out_path.name
    except FFmpegError as e:
        logger.exception("Re-cutting clip %s failed for job %s", index, job_id)
        clips = storage.load_clips(job_id)
        clips[index]["cut_status"] = "error"
        clips[index]["cut_error"] = str(e)
    storage.save_clips(job_id, clips)


@router.patch("/jobs/{job_id}/clips/{index}/trim")
async def trim_clip(
    job_id: str, index: int, background_tasks: BackgroundTasks, body: dict[str, Any] = Body(...)
):
    """
    Adjusts a clip's start/end (e.g. "extend 2s earlier" or "trim 1s off the
    end") and re-cuts it from the original source video. Any existing 9:16
    framing for this clip is invalidated - the footage under it changed, so
    a saved crop plan (and especially any saved time-ranged positions) can
    no longer be trusted to line up, and the finished reel came from the old
    footage. The user re-does framing after a timing change; this endpoint
    only touches the flat/landscape cut.
    """
    job = storage.load_job(job_id)
    if job is None:
        raise HTTPException(404, "Job not found.")
    clips = storage.load_clips(job_id)
    if not clips or index < 0 or index >= len(clips):
        raise HTTPException(404, "Clip not found.")

    try:
        new_start = float(body["start"])
        new_end = float(body["end"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(400, "Body must include numeric 'start' and 'end' seconds.")

    duration = (job.get("media_info") or {}).get("duration_seconds")
    if duration is None:
        raise HTTPException(400, "Source video duration is unknown - re-probe the video first.")

    if new_start < -0.001 or new_end > duration + 0.001:
        raise HTTPException(
            400, f"Timing must stay within the source video (0s to {duration:.2f}s)."
        )
    if new_end - new_start < MIN_CLIP_SECONDS:
        raise HTTPException(400, f"A clip must be at least {MIN_CLIP_SECONDS:.0f}s long.")

    new_start = max(0.0, round(new_start, 2))
    new_end = min(duration, round(new_end, 2))

    clips[index]["start"] = new_start
    clips[index]["end"] = new_end
    clips[index]["cut_status"] = "pending"
    clips[index]["cut_error"] = None
    clips[index]["output_filename"] = None
    # The old crop/framing was chosen against the old footage - it no longer
    # applies, so clear it rather than leaving a stale, mismatched reel.
    clips[index]["reframe_status"] = None
    clips[index]["reframe_error"] = None
    clips[index]["reel_filename"] = None
    # Style and captions were rendered from the old footage too (through the
    # old reel, or through the old styled reel) - without this, the stale
    # styled/captioned file stays marked "done" and best_reel_input_path()
    # keeps serving it, which looks like the timing change was ignored.
    storage.invalidate_downstream_renders(job_id, clips[index], index)
    storage.save_clips(job_id, clips)
    storage.frame_plan_path(job_id, index).unlink(missing_ok=True)

    background_tasks.add_task(_recut_one_clip, job_id, index)
    return {"clip": clips[index]}


@router.get("/jobs/{job_id}/source-file")
async def download_source(job_id: str):
    """
    Serves the original uploaded video, inline, so the timing-adjustment
    editor can scrub through footage just outside a clip's current
    boundaries (the already-cut short clip file doesn't contain that
    footage at all).
    """
    job = storage.load_job(job_id)
    if job is None:
        raise HTTPException(404, "Job not found.")
    path = storage.job_dir(job_id) / job["stored_filename"]
    if not path.exists():
        raise HTTPException(404, "Source video file missing on disk.")
    return FileResponse(path, media_type="video/mp4", content_disposition_type="inline")


def _safe_filename(title: str, fallback: str) -> str:
    cleaned = "".join(c for c in title if c.isalnum() or c in " -_").strip()
    return f"{cleaned or fallback}.mp4"


@router.get("/jobs/{job_id}/clips/{index}/file")
async def download_clip(job_id: str, index: int):
    clips = storage.load_clips(job_id)
    if not clips or index < 0 or index >= len(clips):
        raise HTTPException(404, "Clip not found.")

    clip = clips[index]
    if clip.get("cut_status") != "done":
        raise HTTPException(404, "This clip hasn't been cut yet.")

    path = storage.clip_output_path(job_id, index)
    if not path.exists():
        raise HTTPException(404, "Clip file missing on disk.")

    filename = _safe_filename(clip.get("title", ""), f"clip_{index + 1}")
    # inline (not the default "attachment") so this same endpoint also works
    # as a <video src> for in-browser preview (Phase 6's frame editor) - the
    # frontend's actual download links force a save with the `download`
    # attribute instead of relying on this header.
    return FileResponse(path, media_type="video/mp4", filename=filename, content_disposition_type="inline")
