"""
Thin wrappers around the `ffmpeg` / `ffprobe` command-line tools.

Phase 2 introduces this module with two things:
  - probe(): read metadata (duration, resolution, fps, codecs) without
    touching the video, so later phases know what they're working with
    (e.g. is it already 9:16? does it even have audio?).
  - extract_audio(): pull the audio track out to a mono 16kHz WAV file,
    which is both a good "does ffmpeg actually work on this file" check
    and the exact input format Phase 3's transcription model wants.

Every later phase (cutting clips, reframing, captions, rendering) adds
its own function here rather than shelling out to ffmpeg from routers,
so all the ffmpeg command-building lives in one place.
"""
import json
import subprocess
from pathlib import Path
from typing import Any


class FFmpegError(RuntimeError):
    """Raised when an ffmpeg/ffprobe subprocess exits non-zero."""


def _run(cmd: list[str]) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise FFmpegError(
            f"Command failed ({' '.join(cmd)}):\n{result.stderr[-4000:]}"
        )
    return result.stdout


def probe(video_path: Path) -> dict[str, Any]:
    """Return duration, resolution, fps, and codec info for a video file."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(video_path),
    ]
    raw = _run(cmd)
    data = json.loads(raw)

    video_stream = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    audio_stream = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)

    if video_stream is None:
        raise FFmpegError("No video stream found in file.")

    duration = float(data["format"].get("duration") or video_stream.get("duration") or 0)

    # fps often comes as a fraction string like "30000/1001"
    fps_raw = video_stream.get("avg_frame_rate", "0/0")
    num, _, den = fps_raw.partition("/")
    fps = round(int(num) / int(den), 2) if den and int(den) != 0 else 0.0

    return {
        "duration_seconds": round(duration, 2),
        "width": int(video_stream["width"]),
        "height": int(video_stream["height"]),
        "fps": fps,
        "video_codec": video_stream.get("codec_name"),
        "has_audio": audio_stream is not None,
        "audio_codec": audio_stream.get("codec_name") if audio_stream else None,
        "size_bytes": int(data["format"].get("size", 0)),
    }


def extract_audio(video_path: Path, out_wav_path: Path) -> None:
    """
    Extract audio as mono 16kHz PCM WAV - the format faster-whisper (Phase 3)
    expects, and a fast, simple way to confirm ffmpeg can actually read the
    uploaded file end to end.
    """
    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(video_path),
        "-vn",                 # no video
        "-ac", "1",             # mono
        "-ar", "16000",         # 16kHz sample rate
        "-c:a", "pcm_s16le",
        str(out_wav_path),
    ]
    _run(cmd)


def cut_clip(
    source_path: Path,
    output_path: Path,
    start: float,
    end: float,
    has_audio: bool = True,
) -> None:
    """
    Cut a single clip [start, end) (seconds) out of the source video into its
    own .mp4 file, at the source's original resolution/aspect ratio - 9:16
    reframing is a separate step (Phase 6).

    Re-encodes rather than stream-copying: a stream copy can only cut on
    keyframe boundaries, which for a long source video (keyframes every few
    seconds) would make the AI-picked start/end times drift by up to a few
    seconds - noticeable on a 30-60s reel. `-ss` before `-i` still seeks
    quickly to near the target, then ffmpeg decodes accurately from there,
    so this stays fast without sacrificing precision.
    """
    duration = max(0.1, end - start)
    cmd = [
        "ffmpeg",
        "-y",
        "-ss", f"{start:.3f}",
        "-i", str(source_path),
        "-t", f"{duration:.3f}",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
    ]
    if has_audio:
        cmd += ["-c:a", "aac", "-b:a", "128k"]
    else:
        cmd += ["-an"]
    cmd += ["-movflags", "+faststart", str(output_path)]
    _run(cmd)
