"""
Phase 10: "visual DNA" style preset - a reference-video-inspired look that
can be applied to any reel with one call, giving a final styled output
directly (Aveg's own framing: study a reference video's visual DNA -> make
a reusable preset -> apply it to any video, one click, final output).

History of this file, briefly, because the settings below only make sense
in light of it:
  v1: MediaPipe cutout + halftone dithering + a blue duotone body. Aveg
      asked for the halftone and blue gone entirely.
  v2: rembg with its alpha-matting hair-refinement pass turned on - the
      cleanest possible edges, at a real cost of ~1-1.5+ hours for a 30s
      clip. Aveg tried it and asked for a middle ground: faster, and okay
      losing some fine hair detail.
  v3: plain rembg (no alpha matting) + a morphological open/close + light
      feather to clean up the raw per-frame mask, ~8-10 min for a 30s clip,
      plus a progress/ETA display. Worked, but every frame was still
      segmented completely independently - rembg has no memory of the
      previous frame - so the cutout could subtly shift, gain a stray hole,
      or lose/gain a sliver of background from one frame to the next. Aveg
      flagged this directly: she wants "temporally consistent" tracking
      like Adobe's Roto Brush, not independent per-frame guesses.
  v4 (current): replaced rembg entirely with RobustVideoMatting (RVM,
      https://github.com/PeterL1n/RobustVideoMatting) - an open-source
      neural network built for exactly this problem. Unlike rembg, RVM is
      *recurrent*: it carries a small memory state forward from each frame
      to the next (four ConvGRU hidden states), so the subject boundary is
      tracked over time rather than re-guessed from scratch every frame -
      conceptually the same idea as Roto Brush propagating a mask forward
      through the timeline instead of rotoscoping each frame in isolation.
      This directly fixes the flicker/holes/leakage Aveg described.

      RVM also predicts a clean foreground color (not just an alpha mask).
      Using that predicted foreground - instead of the raw source frame -
      as the input to the grayscale step is what Aveg's message calls
      "matte refinement to prevent background leakage": at a semi-
      transparent edge pixel (e.g. a wisp of hair), the raw frame's pixel
      is a blend of subject and background color, but RVM's foreground
      prediction is a matte-refined estimate of just the subject's color,
      so edges look cleaner and don't fringe with background color.

      Bonus, not a tradeoff: benchmarking in the cloud sandbox found RVM
      (resnet50 backbone) runs at roughly half the per-frame time v3's
      plain rembg did at the app's real 1080x1920 canvas - so this version
      is both higher quality AND faster than v3, not quality traded for
      speed. (Aveg did ask to prioritize quality over speed for this
      round, but it's worth noting the speed didn't have to be sacrificed
      to get it.)

Per-frame pipeline:
  1. RVM (resnet50 backbone) processes the frame together with the
     recurrent state carried from the previous frame (zero-initialized on
     the first frame of each render) -> a temporally-tracked alpha mask
     plus a matte-refined foreground color prediction.
  2. A very light Gaussian feather on just the alpha edge (RVM's raw alpha
     is already clean - this is a small safety margin, not a fix for
     jagged per-frame noise the way v3's morphological cleanup was).
  3. Grayscale the RVM foreground prediction (not the raw frame - see
     above) with a gentle autocontrast stretch - no dithering, no halftone
     screen, no color anywhere in the output.
  4. Composite onto pure white using the alpha mask.
  5. Blend in one static, very-low-strength paper-grain texture (generated
     once per render call and reused on every frame, so it reads as a
     fixed paper surface rather than flickering per-frame noise).

Runs entirely locally via onnxruntime + opencv + Pillow - no torch/GPU
required, same lightweight stack the rest of the app already uses. RVM's
resnet50 ONNX weights (~102MB) download once on first use, from RVM's
GitHub releases, and are cached under this project's data/ folder
afterward - the one network dependency this module has, and only on its
very first run.
"""
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Callable, Optional

import cv2
import numpy as np
from PIL import Image, ImageOps

from .config import DATA_DIR
from .ffmpeg_utils import FFmpegError, probe

# RVM's resnet50 backbone: per RVM's own docs, "small performance
# improvements" over their mobilenetv3 backbone. Benchmarked both in the
# cloud sandbox at the app's real 1080x1920 canvas - resnet50 ran ~0.35s/
# frame vs mobilenetv3's ~0.17s/frame, both dramatically faster than v3's
# rembg (~0.65-0.7s/frame), so there's room to spend on the better backbone
# without landing anywhere near v3's timing, let alone v2's. If a future
# clip ever needs to be faster, swapping _MODEL_NAME to "mobilenetv3" below
# (and the URL/filename to match) is a one-line change - same ONNX I/O
# contract, no other code changes needed.
_MODEL_NAME = "resnet50"
_MODEL_FILENAME = "rvm_resnet50_fp32.onnx"
_MODEL_URL = (
    "https://github.com/PeterL1n/RobustVideoMatting/releases/download/v1.0.0/rvm_resnet50_fp32.onnx"
)
_MODEL_DIR = DATA_DIR / "models"
_MODEL_PATH = _MODEL_DIR / _MODEL_FILENAME

_PAPER = 255  # pure white background/paper

# RVM's downsample_ratio: the model resizes down internally for its first
# (tracking) stage, then refines at full resolution for its second stage.
# RVM's own guidance is to keep the downsampled short side roughly between
# 256 and 512px; 384 is the middle of that range, and computing the ratio
# from the actual input resolution (rather than a fixed constant) means
# this keeps working sensibly whatever resolution a reel ends up being.
_DOWNSAMPLE_TARGET_PX = 384.0

# A very light feather on the alpha edge - RVM's raw alpha is already clean
# (unlike v3's rembg masks, it doesn't need morphological open/close to
# remove speckle), this is only a small anti-aliasing margin, not a fix for
# noisy segmentation. Lowered from 0.6 - Aveg found the edge read as too
# soft/"glitchy" rather than a clean, smooth silhouette line; 0.3 keeps
# just enough softening to avoid a jagged, pixel-stepped edge without the
# wider soft halo the higher value produced. (Tried bumping the model's
# downsample_ratio target instead, on the theory a higher-res tracking
# stage would sharpen the edge more directly - benchmarked ~60% slower at
# 512px vs 384px, and a side-by-side crop showed no visible difference, so
# that wasn't it; RVM's refiner stage already works at full resolution
# regardless of this setting. Left downsample target at 384.)
_EDGE_FEATHER_SIGMA = 0.3

# RVM is trained purely for HUMAN alpha matting - it has no concept of
# "things the person is holding," so a mic, cup, phone, etc. often gets
# only partial alpha confidence (RVM is unsure whether it's part of the
# subject) rather than being included fully, and can look cut away or
# ghostly at its edges. This is a heuristic fix, not true object-aware
# segmentation (a person+held-object model is well beyond a local/free
# tool's scope): a gamma curve (values < 1) pulls the whole alpha channel
# toward full opacity, with the biggest boost in the uncertain midtones -
# exactly where "RVM has SOME confidence this is part of the subject"
# pixels sit - while leaving true background (already ~0) and the
# obviously-the-subject core (already ~1) almost untouched. 0.6 was chosen
# as a moderate boost: strong enough to pull a partially-recognized held
# object closer to fully-opaque without also dragging genuinely-background
# pixels into the cutout. Applied before the edge feather below.
#
# A connectivity-based second pass (grow a confident region through any
# touching low-alpha blob, however far it extends) was tried after Aveg
# reported a mic still rendering as a pale ghost under this gamma alone -
# but the result looked worse to her, so it was reverted. Back to gamma
# only, unchanged from before that attempt.
_HELD_OBJECT_ALPHA_GAMMA = 0.6

# Paper-grain texture. v3 shipped at amplitude 12 based on sandbox crop
# tests, but Aveg asked again on real hardware for the paper texture to be
# added/more visible - bumped further here. H.264 tends to smooth away
# fine, low-amplitude noise like this, so alongside the amplitude bump the
# encode CRF below was also dropped a couple of steps (less compression)
# specifically so this texture survives encoding instead of being
# flattened out.
_PAPER_GRAIN_AMPLITUDE = 20.0
_PAPER_GRAIN_BLUR_SIGMA = 0.6

_session = None


class StyleDNAError(RuntimeError):
    pass


ProgressCallback = Callable[[int, int], None]  # (frames_done, frames_total) -> None


def _ensure_model() -> Path:
    """Downloads RVM's resnet50 ONNX weights on first use and caches them
    under this project's data/ folder - mirrors how rembg's own model
    caching worked in v2/v3, just self-managed since RVM has no pip
    package to do it for us."""
    if _MODEL_PATH.exists() and _MODEL_PATH.stat().st_size > 0:
        return _MODEL_PATH
    _MODEL_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = _MODEL_PATH.with_suffix(".onnx.part")
    try:
        urllib.request.urlretrieve(_MODEL_URL, tmp_path)
    except Exception as e:
        tmp_path.unlink(missing_ok=True)
        raise StyleDNAError(
            f"Could not download the RVM {_MODEL_NAME} model (~102MB, one-time download) "
            f"from {_MODEL_URL}: {e}. Check your internet connection and try again."
        ) from e
    tmp_path.rename(_MODEL_PATH)
    return _MODEL_PATH


def _get_session():
    global _session
    if _session is None:
        import onnxruntime as ort

        model_path = _ensure_model()
        # Prefer the GPU via DirectML when it's actually available - any
        # DirectX 12 GPU (NVIDIA/AMD/Intel Arc), no CUDA toolkit install
        # needed, unlike onnxruntime's CUDA provider. A ResNet50-sized
        # model like RVM's runs several times faster on a GPU than on even
        # a strong CPU, which is what a 30-40+ minute style render on real
        # hardware pointed to - a fast laptop CPU alone still isn't fast
        # enough for a per-frame neural net at 1080x1920. DirectML support
        # requires the `onnxruntime-directml` package specifically (see
        # requirements.txt) - it and plain `onnxruntime` can't both be
        # installed at once, so `get_available_providers()` below is what
        # actually decides whether GPU is even attempted, not a guess:
        # plain `onnxruntime` (this project's cloud sandbox, or a machine
        # that hasn't switched packages yet) never lists
        # "DmlExecutionProvider", so it stays CPU-only automatically.
        providers = ["CPUExecutionProvider"]
        try:
            available = ort.get_available_providers()
        except Exception:
            available = []
        if "DmlExecutionProvider" in available:
            providers = ["DmlExecutionProvider", "CPUExecutionProvider"]
        try:
            _session = ort.InferenceSession(str(model_path), providers=providers)
        except Exception:
            # Defensive fallback: DirectML being *listed* as available
            # doesn't guarantee it actually initializes cleanly (a stale
            # GPU driver, a DirectX runtime hiccup, etc.) - retry CPU-only
            # rather than letting a GPU problem take down style rendering
            # entirely.
            _session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
    return _session


def reset_model() -> None:
    """Drops the cached onnxruntime session. Not needed in normal use (one
    render call handles a whole video); exists so a long-running process -
    e.g. tests - can free it between unrelated videos."""
    global _session
    _session = None


def _make_paper_grain(h: int, w: int, seed: int = 0) -> np.ndarray:
    """A single HxW float32 grain texture, mean 0, to add to every frame's
    brightness. Fixed seed so re-rendering the same video is reproducible."""
    rng = np.random.default_rng(seed)
    noise = rng.normal(loc=0.0, scale=_PAPER_GRAIN_AMPLITUDE, size=(h, w)).astype(np.float32)
    if _PAPER_GRAIN_BLUR_SIGMA > 0:
        noise = cv2.GaussianBlur(noise, ksize=(0, 0), sigmaX=_PAPER_GRAIN_BLUR_SIGMA)
    return noise


def _downsample_ratio_for(h: int, w: int) -> float:
    short_side = min(h, w)
    ratio = _DOWNSAMPLE_TARGET_PX / short_side
    return float(np.clip(ratio, 0.05, 1.0))


def _style_frame(fgr_bgr: np.ndarray, alpha01: np.ndarray, grain: np.ndarray) -> np.ndarray:
    """Returns one styled BGR frame: clean grayscale cutout of the subject
    (from RVM's matte-refined foreground prediction, not the raw frame),
    composited onto white with paper grain, no color, no halftone."""
    if _HELD_OBJECT_ALPHA_GAMMA != 1.0:
        alpha01 = np.power(np.clip(alpha01, 0.0, 1.0), _HELD_OBJECT_ALPHA_GAMMA)
    if _EDGE_FEATHER_SIGMA > 0:
        alpha01 = cv2.GaussianBlur(alpha01, ksize=(0, 0), sigmaX=_EDGE_FEATHER_SIGMA)

    gray = cv2.cvtColor(fgr_bgr, cv2.COLOR_BGR2GRAY)
    # A gentle autocontrast stretch - not a heavy curve - so the portrait
    # reads as a clean, natural black-and-white rather than a flat photo.
    gray = np.asarray(ImageOps.autocontrast(Image.fromarray(gray), cutoff=1))

    composited = _PAPER - alpha01 * (_PAPER - gray.astype(np.float32))
    composited = np.clip(composited + grain, 0, 255).astype(np.uint8)

    return cv2.cvtColor(composited, cv2.COLOR_GRAY2BGR)


def render_style_dna_video(
    input_path: Path, output_path: Path, progress_cb: Optional[ProgressCallback] = None
) -> None:
    """
    Applies the reference-style visual DNA (clean, temporally-tracked
    subject cutout, pure black-and-white rendering, white background,
    subtle paper grain) to every frame of input_path, muxes the original
    audio back in unchanged, and writes an h264/aac mp4 to output_path.

    Frames are processed sequentially and RVM's recurrent state is carried
    from one frame to the next (reset to zero at the start of each call),
    which is what gives the subject boundary temporal stability instead of
    each frame being segmented independently.

    If given, progress_cb(frames_done, frames_total) is called periodically
    (throttled to roughly once a second of wall-clock time, not every
    frame, since it's typically used to persist progress to disk) so the
    caller can show an ETA.
    """
    info = probe(input_path)
    full_w, full_h = info["width"], info["height"]
    fps = info["fps"] or 30.0
    if full_w <= 0 or full_h <= 0:
        raise StyleDNAError(f"Could not read a valid resolution for {input_path}.")

    session = _get_session()
    downsample_ratio = _downsample_ratio_for(full_h, full_w)
    dsr_input = np.array([downsample_ratio], dtype=np.float32)
    rec = [np.zeros([1, 1, 1, 1], dtype=np.float32)] * 4  # RVM's 4 recurrent (ConvGRU) states

    cap = cv2.VideoCapture(str(input_path))
    if not cap.isOpened():
        raise StyleDNAError(f"Could not open {input_path} for reading.")

    frames_total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    if frames_total <= 0:
        frames_total = max(1, round(info["duration_seconds"] * fps))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg_cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{full_w}x{full_h}", "-r", f"{fps}",
        "-i", "pipe:0",
        "-i", str(input_path),
        "-map", "0:v", "-map", "1:a?",
        # crf 17 (down from 20): less compression, so the fine paper-grain
        # texture survives encoding instead of being smoothed away.
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "17", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-shortest",
        "-movflags", "+faststart",
        str(output_path),
    ]
    proc = subprocess.Popen(ffmpeg_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    grain = _make_paper_grain(full_h, full_w)
    frame_i = 0
    last_progress_at = 0.0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
            src = np.transpose(rgb, (2, 0, 1))[None]  # 1,C,H,W
            fgr, pha, *rec = session.run(
                [],
                {
                    "src": src,
                    "r1i": rec[0], "r2i": rec[1], "r3i": rec[2], "r4i": rec[3],
                    "downsample_ratio": dsr_input,
                },
            )
            alpha01 = pha[0, 0]
            fgr_rgb = np.clip(fgr[0].transpose(1, 2, 0) * 255.0, 0, 255).astype(np.uint8)
            fgr_bgr = cv2.cvtColor(fgr_rgb, cv2.COLOR_RGB2BGR)

            styled = _style_frame(fgr_bgr, alpha01, grain)
            proc.stdin.write(styled.tobytes())
            frame_i += 1

            if progress_cb is not None:
                now = time.monotonic()
                if now - last_progress_at >= 1.0:
                    progress_cb(frame_i, max(frame_i, frames_total))
                    last_progress_at = now
    finally:
        cap.release()
        # communicate() closes stdin itself before reading stdout/stderr and
        # waiting on the process - closing it ourselves first makes that
        # second close raise "flush of closed file".
        _, stderr = proc.communicate()

    if progress_cb is not None:
        progress_cb(frame_i, frame_i)

    if frame_i == 0:
        raise StyleDNAError("No frames were read from the input video.")
    if proc.returncode != 0:
        raise StyleDNAError(
            f"ffmpeg failed while encoding the styled video:\n{stderr.decode(errors='replace')[-4000:]}"
        )
