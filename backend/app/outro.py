"""
Add-outro: appends a fixed branded outro clip to the end of a reel, one
click. Not a per-clip customizable thing - it's the same short "Layover"
logo sting every time, bundled with the app under app/assets/outro.mp4,
the way the caption system's fonts are bundled rather than fetched.

Always applies to the clip's MOST FINISHED reel at the moment it's clicked -
captioned > styled > plain 9:16 - not a fixed stage in the pipeline, since
whether style/captions exist yet varies per clip. Because of that, this
result goes stale exactly like style/captions do when the footage under
them changes: see storage.invalidate_outro, called both from
storage.invalidate_downstream_renders (a trim/reframe changed the footage
entirely) and directly from the style/caption render routers (re-applying
either one changes what "most finished" means, even without a trim/reframe).

Rendering: ffmpeg's concat filter (not the concat demuxer) - it re-encodes
both inputs into a shared format instead of requiring them to already be
byte-identical in codec/resolution/fps, which is worth the small extra
render time for one-click reliability regardless of how the main reel was
produced. Since the main reel is always 1080x1920 already (this app's fixed
canvas) and the bundled outro is authored at that same size, the scale/pad
step below is a no-op safety net, not doing real work.
"""
from pathlib import Path

from .ffmpeg_utils import FFmpegError, _run, probe

OUTRO_ASSET_PATH = Path(__file__).parent / "assets" / "outro.mp4"


class OutroError(RuntimeError):
    pass


def render_with_outro(main_path: Path, out_path: Path, canvas: dict[str, int]) -> None:
    """main_path (a finished 9:16 reel) -> out_path (the same, with the bundled outro appended)."""
    if not OUTRO_ASSET_PATH.exists():
        raise OutroError("The bundled outro clip is missing from the app (app/assets/outro.mp4).")

    try:
        main_info = probe(main_path)
    except FFmpegError as e:
        raise OutroError(f"Could not read the main reel: {e}") from e
    try:
        outro_info = probe(OUTRO_ASSET_PATH)
    except FFmpegError as e:
        raise OutroError(f"Could not read the bundled outro clip: {e}") from e

    cw, ch = canvas["width"], canvas["height"]
    has_main_audio = main_info["has_audio"]
    has_outro_audio = outro_info["has_audio"]

    def _video_chain(input_idx: int, label: str) -> str:
        return (
            f"[{input_idx}:v]scale={cw}:{ch}:force_original_aspect_ratio=decrease,"
            f"pad={cw}:{ch}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,format=yuv420p[{label}]"
        )

    filters = [_video_chain(0, "v0"), _video_chain(1, "v1")]

    if has_main_audio and has_outro_audio:
        filters.append("[0:a]aformat=sample_rates=48000:channel_layouts=stereo[a0]")
        filters.append("[1:a]aformat=sample_rates=48000:channel_layouts=stereo[a1]")
        filters.append("[v0][a0][v1][a1]concat=n=2:v=1:a=1[outv][outa]")
        maps, has_audio_out = ["-map", "[outv]", "-map", "[outa]"], True
    elif has_main_audio and not has_outro_audio:
        # The outro has no audio track of its own - pad it with silence so
        # concat still has a matching audio stream on both sides.
        filters.append("[0:a]aformat=sample_rates=48000:channel_layouts=stereo[a0]")
        filters.append(f"anullsrc=channel_layout=stereo:sample_rate=48000:d={outro_info['duration_seconds']}[a1]")
        filters.append("[v0][a0][v1][a1]concat=n=2:v=1:a=1[outv][outa]")
        maps, has_audio_out = ["-map", "[outv]", "-map", "[outa]"], True
    elif not has_main_audio and has_outro_audio:
        filters.append(f"anullsrc=channel_layout=stereo:sample_rate=48000:d={main_info['duration_seconds']}[a0]")
        filters.append("[1:a]aformat=sample_rates=48000:channel_layouts=stereo[a1]")
        filters.append("[v0][a0][v1][a1]concat=n=2:v=1:a=1[outv][outa]")
        maps, has_audio_out = ["-map", "[outv]", "-map", "[outa]"], True
    else:
        filters.append("[v0][v1]concat=n=2:v=1:a=0[outv]")
        maps, has_audio_out = ["-map", "[outv]"], False

    cmd = [
        "ffmpeg", "-y",
        "-i", str(main_path),
        "-i", str(OUTRO_ASSET_PATH),
        "-filter_complex", ";".join(filters),
        *maps,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
    ]
    if has_audio_out:
        cmd += ["-c:a", "aac", "-b:a", "128k"]
    cmd += ["-movflags", "+faststart", str(out_path)]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        _run(cmd)
    except FFmpegError as e:
        raise OutroError(f"Appending the outro failed: {e}") from e
