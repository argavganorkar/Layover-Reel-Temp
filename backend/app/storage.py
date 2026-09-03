"""
Tiny on-disk "database" for jobs.

Since this is a personal single-user tool, we don't need Postgres/SQLite/etc.
Each job is just a folder containing a job.json file. This module reads and
writes that file. It's intentionally simple.
"""
import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .config import UPLOADS_DIR


def new_job_id() -> str:
    return uuid.uuid4().hex[:12]


def job_dir(job_id: str) -> Path:
    return UPLOADS_DIR / job_id


def job_json_path(job_id: str) -> Path:
    return job_dir(job_id) / "job.json"


def transcript_json_path(job_id: str) -> Path:
    return job_dir(job_id) / "transcript.json"


def save_transcript(job_id: str, transcript: dict[str, Any]) -> None:
    transcript_json_path(job_id).write_text(json.dumps(transcript, indent=2))


def load_transcript(job_id: str) -> Optional[dict[str, Any]]:
    path = transcript_json_path(job_id)
    if not path.exists():
        return None
    return json.loads(path.read_text())


def clips_json_path(job_id: str) -> Path:
    return job_dir(job_id) / "clips.json"


def save_clips(job_id: str, clips: list[dict[str, Any]]) -> None:
    clips_json_path(job_id).write_text(json.dumps(clips, indent=2))


def load_clips(job_id: str) -> Optional[list[dict[str, Any]]]:
    path = clips_json_path(job_id)
    if not path.exists():
        return None
    return json.loads(path.read_text())


def clips_dir(job_id: str) -> Path:
    """Where cut clip .mp4 files (Phase 5+) live, one per selected clip."""
    return job_dir(job_id) / "clips"


def clip_output_path(job_id: str, index: int) -> Path:
    """Output file path for the clip at `index` in clips.json (0-based)."""
    return clips_dir(job_id) / f"clip_{index + 1}.mp4"


def frames_dir(job_id: str) -> Path:
    """Where per-clip frame plans (Phase 6 crop/layout choices) live."""
    return job_dir(job_id) / "frames"


def frame_plan_path(job_id: str, index: int) -> Path:
    return frames_dir(job_id) / f"clip_{index + 1}.json"


def save_frame_plan(job_id: str, index: int, plan: dict[str, Any]) -> None:
    frames_dir(job_id).mkdir(parents=True, exist_ok=True)
    frame_plan_path(job_id, index).write_text(json.dumps(plan, indent=2))


def load_frame_plan(job_id: str, index: int) -> Optional[dict[str, Any]]:
    path = frame_plan_path(job_id, index)
    if not path.exists():
        return None
    return json.loads(path.read_text())


def reels_dir(job_id: str) -> Path:
    """Where final rendered 9:16 reels (Phase 6 output) live, one per clip."""
    return job_dir(job_id) / "reels"


def reel_output_path(job_id: str, index: int) -> Path:
    return reels_dir(job_id) / f"reel_{index + 1}.mp4"


def styled_reels_dir(job_id: str) -> Path:
    """Where reels with the Phase 10 reference-style visual DNA applied
    live, one per clip - a styled version of the plain 9:16 reel, used as
    the caption stage's input in place of the reel when present."""
    return job_dir(job_id) / "styled_reels"


def styled_reel_output_path(job_id: str, index: int) -> Path:
    return styled_reels_dir(job_id) / f"reel_{index + 1}.mp4"


def best_reel_input_path(job_id: str, index: int, clip: dict[str, Any]) -> Path:
    """
    The base video the caption stage should render onto: the styled reel if
    Phase 10's visual-DNA style has been applied to this clip, otherwise the
    plain reframed reel. Centralized here so caption rendering automatically
    picks up a style applied before or after a caption plan already exists.
    """
    if clip.get("style_status") == "done":
        styled = styled_reel_output_path(job_id, index)
        if styled.exists():
            return styled
    return reel_output_path(job_id, index)


def captions_dir(job_id: str) -> Path:
    """Where per-clip caption "beat" plans (Phase 8) live - LLM-generated or user-edited."""
    return job_dir(job_id) / "captions"


def caption_plan_path(job_id: str, index: int) -> Path:
    return captions_dir(job_id) / f"clip_{index + 1}.json"


def save_caption_plan(job_id: str, index: int, beats: list[dict[str, Any]]) -> None:
    captions_dir(job_id).mkdir(parents=True, exist_ok=True)
    caption_plan_path(job_id, index).write_text(json.dumps(beats, indent=2))


def load_caption_plan(job_id: str, index: int) -> Optional[list[dict[str, Any]]]:
    path = caption_plan_path(job_id, index)
    if not path.exists():
        return None
    return json.loads(path.read_text())


def captioned_reels_dir(job_id: str) -> Path:
    """Where final reels with burned-in captions (Phase 8 output) live, one per clip."""
    return job_dir(job_id) / "captioned_reels"


def captioned_reel_output_path(job_id: str, index: int) -> Path:
    return captioned_reels_dir(job_id) / f"reel_{index + 1}.mp4"


def outro_reels_dir(job_id: str) -> Path:
    """Where reels with the bundled outro appended live, one per clip."""
    return job_dir(job_id) / "outro_reels"


def outro_reel_output_path(job_id: str, index: int) -> Path:
    return outro_reels_dir(job_id) / f"reel_{index + 1}.mp4"


def most_finished_reel_path(job_id: str, index: int, clip: dict[str, Any]) -> Path:
    """
    The most finished version of a clip's reel available right now, in
    priority order: captioned > styled > plain 9:16 reel - mirrors the
    frontend's ResultsGallery.bestResultFor. Used as the outro stage's
    input, so "add outro" always appends to whatever's actually finished,
    however this particular clip got there (style only, captions only,
    both, or neither).
    """
    if clip.get("caption_status") == "done":
        p = captioned_reel_output_path(job_id, index)
        if p.exists():
            return p
    if clip.get("style_status") == "done":
        p = styled_reel_output_path(job_id, index)
        if p.exists():
            return p
    return reel_output_path(job_id, index)


def invalidate_outro(job_id: str, clip: dict[str, Any], index: int) -> None:
    """
    Clears a clip's outro result. The outro is baked onto whatever was the
    MOST FINISHED reel at the moment "Add outro" was clicked (see
    most_finished_reel_path) - so it goes stale not just when the footage
    itself changes (a trim/reframe - handled by
    invalidate_downstream_renders below, which includes this call) but also
    when style or captions are (re)applied afterward, since that changes
    what "most finished" pointed to. Call this directly before starting a
    style or caption render too, not just on trim/reframe.
    """
    clip["outro_status"] = None
    clip["outro_error"] = None
    clip["outro_reel_filename"] = None
    outro_reel_output_path(job_id, index).unlink(missing_ok=True)


def invalidate_downstream_renders(job_id: str, clip: dict[str, Any], index: int) -> None:
    """
    Clears a clip's style, caption, and outro results (both the status
    fields on `clip`, mutated in place, and the actual rendered files on
    disk) because something upstream - the cut timing (routers/cut.py's
    trim_clip) or the 9:16 frame plan (routers/reframe.py's start_reframe) -
    just changed.

    Both of those stages previously only invalidated their own immediate
    downstream step (e.g. a timing change cleared reframe_status) but left
    style_status/caption_status sitting at "done" - so best_reel_input_path()
    kept quietly serving the OLD styled/captioned file, rendered from the
    OLD footage, right alongside a freshly re-cut or re-framed reel. From
    the outside that looked like "adjusting the frame/time and applying the
    filter does nothing - it just shows the old default video," which is
    exactly what was happening: the stale styled file was still there and
    still marked done, so it kept winning.

    Call this BEFORE storage.save_clips() so the invalidation lands in the
    same write as whatever field change triggered it - the caller mutates
    `clip` (an entry from a list already loaded via load_clips) and this
    function mutates it further in place.
    """
    clip["style_status"] = None
    clip["style_error"] = None
    clip["styled_reel_filename"] = None
    styled_reel_output_path(job_id, index).unlink(missing_ok=True)

    clip["caption_status"] = None
    clip["caption_error"] = None
    clip["captioned_reel_filename"] = None
    captioned_reel_output_path(job_id, index).unlink(missing_ok=True)

    invalidate_outro(job_id, clip, index)


def create_job(original_filename: str, size_bytes: int, stored_filename: str) -> dict[str, Any]:
    job_id = new_job_id()
    d = job_dir(job_id)
    d.mkdir(parents=True, exist_ok=False)

    job = {
        "id": job_id,
        "original_filename": original_filename,
        "stored_filename": stored_filename,
        "size_bytes": size_bytes,
        "status": "uploaded",  # uploaded -> transcribing -> finding_moments -> cutting -> reframing -> captioning -> rendering -> complete -> error
        "error": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    save_job(job)
    return job


def save_job(job: dict[str, Any]) -> None:
    job["updated_at"] = datetime.now(timezone.utc).isoformat()
    path = job_json_path(job["id"])
    path.write_text(json.dumps(job, indent=2))


def load_job(job_id: str) -> Optional[dict[str, Any]]:
    path = job_json_path(job_id)
    if not path.exists():
        return None
    return json.loads(path.read_text())


def list_jobs() -> list[dict[str, Any]]:
    jobs = []
    if not UPLOADS_DIR.exists():
        return jobs
    for d in UPLOADS_DIR.iterdir():
        if d.is_dir():
            j = load_job(d.name)
            if j:
                jobs.append(j)
    jobs.sort(key=lambda j: j["created_at"], reverse=True)
    return jobs


def delete_job(job_id: str) -> bool:
    """
    Deletes a job's entire folder - source video, transcript, clips, reels,
    captions, everything derived from it. Irreversible; the router asks the
    user to confirm before calling this. Returns False if the job didn't
    exist (already deleted / bad id), True on success.
    """
    d = job_dir(job_id)
    if not d.exists():
        return False
    shutil.rmtree(d)
    return True


def update_status(job_id: str, status: str, error: Optional[str] = None) -> dict[str, Any]:
    job = load_job(job_id)
    if job is None:
        raise FileNotFoundError(job_id)
    job["status"] = status
    job["error"] = error
    save_job(job)
    return job
