"""
Phase 6: reframe a cut clip (Phase 5's output, still in its original
landscape aspect ratio) into a final 9:16 vertical video - the actual final
output of this whole tool, not just a preview.

Unlike auto speaker-tracking, this gives the user full manual control over
the framing, similar to the split-screen crop editors in tools like Opus
Clip: they choose a "layout" (how many stacked regions make up the 9:16
frame - one for a single centered/zoomed shot, two for a split-screen with
two people, three for a trio, or a letterboxed "horizontal" fit with no
crop at all) and drag a crop box per region. They can also change the
layout/boxes at different points within a single clip (e.g. pan from one
speaker to another partway through).

Data model - a "frame plan" for one clip is:
    {
      "canvas": {"width": 1080, "height": 1920},
      "positions": [
        {
          "start": 0.0,        # seconds, relative to the clip's own start
          "end": 5.0,
          "layout": "vertical",   # label only - UI/preset metadata
          "mode": "crop",         # "crop" (1-3 boxes, stacked) or "letterbox" (no crop)
          "boxes": [
            {"x": 0.1, "y": 0.0, "width": 0.5, "height": 1.0}  # normalized 0-1, source clip coords
          ]
        },
        ...
      ]
    }

`positions` must cover [0, clip_duration) contiguously with no gaps or
overlaps - the UI is responsible for keeping them that way; the backend
validates this strictly rather than silently guessing what the user meant.

Rendering strategy, chosen for reliability over cleverness: each position
is rendered as its own fully-encoded temp segment (crop/letterbox baked in
via ffmpeg's `crop`/`scale`/`pad` filters - no fragile time-varying filter
expressions), then all segments are joined with ffmpeg's concat demuxer.
Since every segment is encoded with identical codec/resolution/fps
settings, the concat step is a fast, lossless stream copy. A clip with only
one position (the common case) skips segmenting entirely and renders
straight to the final output.
"""
import json
import tempfile
from pathlib import Path
from typing import Any, Literal, Optional

from .ffmpeg_utils import FFmpegError, _run, probe

Layout = Literal["vertical", "free", "centered", "spotlight", "split", "trio", "horizontal"]
Mode = Literal["crop", "letterbox"]

DEFAULT_CANVAS = {"width": 1080, "height": 1920}
MAX_BOXES_PER_POSITION = 3
_COVERAGE_TOLERANCE = 0.05  # seconds of slack allowed when checking position coverage


class ReframeError(RuntimeError):
    pass


def default_frame_plan(clip_width: int, clip_height: int, clip_duration: float) -> dict[str, Any]:
    """
    A sensible starting point before the user has customized anything: one
    "vertical" position spanning the whole clip, with a single box centered
    on the source frame and cropped to the canvas's own aspect ratio (so it
    fills the 9:16 frame with no distortion) - just the biggest centered
    slice of the original video, which is the simple/reliable fallback the
    original spec asked for if nothing fancier is chosen.
    """
    canvas = DEFAULT_CANVAS
    target_ar = canvas["width"] / canvas["height"]
    source_ar = clip_width / clip_height if clip_height else target_ar

    if source_ar > target_ar:
        box_h = clip_height
        box_w = clip_height * target_ar
    else:
        box_w = clip_width
        box_h = clip_width / target_ar

    box = {
        "x": round((clip_width - box_w) / 2 / clip_width, 4),
        "y": round((clip_height - box_h) / 2 / clip_height, 4),
        "width": round(box_w / clip_width, 4),
        "height": round(box_h / clip_height, 4),
    }

    return {
        "canvas": canvas,
        "positions": [
            {
                "start": 0.0,
                "end": round(clip_duration, 2),
                "layout": "vertical",
                "mode": "crop",
                "boxes": [box],
            }
        ],
    }


def _validate_positions(positions: list[dict[str, Any]], clip_duration: float) -> list[dict[str, Any]]:
    if not positions:
        raise ReframeError("A frame plan needs at least one position.")

    cleaned = sorted(positions, key=lambda p: p["start"])

    if cleaned[0]["start"] > _COVERAGE_TOLERANCE:
        raise ReframeError(
            f"Positions must start at 0s (first position starts at {cleaned[0]['start']}s)."
        )
    if abs(cleaned[-1]["end"] - clip_duration) > max(_COVERAGE_TOLERANCE, clip_duration * 0.02):
        raise ReframeError(
            f"Positions must cover the whole clip - last position ends at "
            f"{cleaned[-1]['end']}s but the clip is {clip_duration:.2f}s long."
        )

    for i, pos in enumerate(cleaned):
        if pos["end"] <= pos["start"]:
            raise ReframeError(f"Position {i} has end <= start.")
        if i > 0 and abs(pos["start"] - cleaned[i - 1]["end"]) > _COVERAGE_TOLERANCE:
            raise ReframeError(
                f"Gap or overlap between position {i - 1} (ends {cleaned[i - 1]['end']}s) "
                f"and position {i} (starts {pos['start']}s) - positions must be contiguous."
            )
        mode = pos.get("mode", "crop")
        if mode not in ("crop", "letterbox"):
            raise ReframeError(f"Position {i} has unknown mode '{mode}'.")
        boxes = pos.get("boxes") or []
        if mode == "crop":
            if not (1 <= len(boxes) <= MAX_BOXES_PER_POSITION):
                raise ReframeError(
                    f"Position {i} has {len(boxes)} boxes - must be 1-{MAX_BOXES_PER_POSITION}."
                )
        else:
            # Letterbox is normally the whole frame (0 boxes), but the
            # "free" layout lets the user pick one region to letterbox
            # instead of the whole source - at most one box, never stacked.
            if len(boxes) > 1:
                raise ReframeError(f"Position {i} is letterboxed but has {len(boxes)} boxes - at most 1 is allowed.")
        for b in boxes:
            for key in ("x", "y", "width", "height"):
                if key not in b:
                    raise ReframeError(f"Position {i} box missing '{key}'.")
            if b["width"] <= 0 or b["height"] <= 0:
                raise ReframeError(f"Position {i} has a box with non-positive size.")
            if b["x"] < -0.001 or b["y"] < -0.001 or b["x"] + b["width"] > 1.001 or b["y"] + b["height"] > 1.001:
                raise ReframeError(f"Position {i} has a box that falls outside the source frame.")

    return cleaned


def _box_crop_filter(box: dict[str, float], source_w: int, source_h: int, target_w: int, target_h: int) -> str:
    """
    Crop the user's chosen region out of the source, then scale+crop it to
    exactly fill (target_w, target_h) with no distortion ("cover" scaling -
    same idea as CSS object-fit: cover), regardless of the box's own aspect
    ratio. This is what makes split/trio layouts (where each region only
    gets a slice of the canvas height) look clean even if the drawn box
    isn't a perfect match for that slice's aspect ratio.
    """
    cw = max(2, round(box["width"] * source_w))
    ch = max(2, round(box["height"] * source_h))
    cx = max(0, min(source_w - cw, round(box["x"] * source_w)))
    cy = max(0, min(source_h - ch, round(box["y"] * source_h)))
    return (
        f"crop={cw}:{ch}:{cx}:{cy},"
        f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
        f"crop={target_w}:{target_h}"
    )


def _box_letterbox_filter(box: dict[str, float], source_w: int, source_h: int, target_w: int, target_h: int) -> str:
    """
    Crop the user's chosen free-form region out of the source, then fit that
    whole region inside (target_w, target_h) preserving its own aspect ratio
    ("contain" - same idea as CSS object-fit: contain, the opposite of
    `_box_crop_filter`'s "cover"), padding whichever axis has room left with
    black. This is the "Free" layout's counterpart to Horizontal's
    whole-frame letterbox - same idea, just on a user-selected sub-region
    instead of the entire source frame, so nothing the user selected is ever
    cropped away, only ever padded.
    """
    cw = max(2, round(box["width"] * source_w))
    ch = max(2, round(box["height"] * source_h))
    cx = max(0, min(source_w - cw, round(box["x"] * source_w)))
    cy = max(0, min(source_h - ch, round(box["y"] * source_h)))
    return (
        f"crop={cw}:{ch}:{cx}:{cy},"
        f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
        f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:black"
    )


def _build_filter_complex(
    position: dict[str, Any], source_w: int, source_h: int, canvas: dict[str, int]
) -> tuple[str, str]:
    """Returns (filter_complex_string, output_video_label)."""
    cw, ch = canvas["width"], canvas["height"]

    if position.get("mode") == "letterbox":
        boxes = position.get("boxes") or []
        if boxes:
            # "Free" layout: letterbox the user's selected region, not the
            # whole source frame.
            filt = f"[0:v]{_box_letterbox_filter(boxes[0], source_w, source_h, cw, ch)}[vout]"
        else:
            filt = (
                f"[0:v]scale={cw}:{ch}:force_original_aspect_ratio=decrease,"
                f"pad={cw}:{ch}:(ow-iw)/2:(oh-ih)/2:black[vout]"
            )
        return filt, "[vout]"

    boxes = position["boxes"]
    n = len(boxes)
    slice_h = ch // n

    parts = []
    labels = []
    for i, box in enumerate(boxes):
        # Last slice absorbs any rounding remainder so slices sum exactly to ch.
        h = ch - slice_h * (n - 1) if i == n - 1 else slice_h
        filt = _box_crop_filter(box, source_w, source_h, cw, h)
        label = f"b{i}"
        parts.append(f"[0:v]{filt}[{label}]")
        labels.append(f"[{label}]")

    if n == 1:
        # Rename the single box's output to vout directly - no stack needed.
        parts[0] = parts[0].replace("[b0]", "[vout]")
        return ";".join(parts), "[vout]"

    stack = "".join(labels) + f"vstack=inputs={n}[vout]"
    parts.append(stack)
    return ";".join(parts), "[vout]"


def _render_position(
    source_clip_path: Path,
    position: dict[str, Any],
    source_w: int,
    source_h: int,
    canvas: dict[str, int],
    out_path: Path,
    has_audio: bool,
) -> None:
    start, end = position["start"], position["end"]
    duration = max(0.1, end - start)
    filter_complex, out_label = _build_filter_complex(position, source_w, source_h, canvas)

    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{start:.3f}",
        "-i", str(source_clip_path),
        "-t", f"{duration:.3f}",
        "-filter_complex", filter_complex,
        "-map", out_label,
    ]
    if has_audio:
        cmd += ["-map", "0:a?", "-c:a", "aac", "-b:a", "128k"]
    cmd += [
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-r", "30",
        str(out_path),
    ]
    _run(cmd)


def _concat(segment_paths: list[Path], out_path: Path) -> None:
    list_file = out_path.parent / f"{out_path.stem}_concat_list.txt"
    list_file.write_text("\n".join(f"file '{p.resolve()}'" for p in segment_paths))
    try:
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(list_file),
            "-c", "copy",
            str(out_path),
        ]
        _run(cmd)
    finally:
        list_file.unlink(missing_ok=True)


def render_reel(
    source_clip_path: Path,
    frame_plan: dict[str, Any],
    out_path: Path,
) -> None:
    """Render a clip's frame plan into the final 9:16 output at out_path."""
    info = probe(source_clip_path)
    source_w, source_h = info["width"], info["height"]
    has_audio = info["has_audio"]
    canvas = frame_plan.get("canvas") or DEFAULT_CANVAS

    positions = _validate_positions(frame_plan["positions"], info["duration_seconds"])

    out_path.parent.mkdir(parents=True, exist_ok=True)

    if len(positions) == 1:
        _render_position(source_clip_path, positions[0], source_w, source_h, canvas, out_path, has_audio)
        return

    with tempfile.TemporaryDirectory(dir=out_path.parent) as tmp:
        tmp_dir = Path(tmp)
        segment_paths = []
        for i, pos in enumerate(positions):
            seg_path = tmp_dir / f"segment_{i}.mp4"
            _render_position(source_clip_path, pos, source_w, source_h, canvas, seg_path, has_audio)
            segment_paths.append(seg_path)
        _concat(segment_paths, out_path)
