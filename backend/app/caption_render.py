"""
Phase 8: renders a caption "beat" plan (see captions.py) as burned-in
captions on top of an already-reframed 9:16 reel.

Visual system: a fixed, disciplined THREE-TIER hierarchy, not a per-beat
style choice. Every beat has a `role` - "setup" (small elegant serif
italic), "punch" (large bold condensed sans), or "accent" (rare blue
script) - and that role alone determines its entire look via _ROLE_STYLE
below. This replaced an earlier version where the LLM picked font/weight/
fill/color/rotation/animation per beat from a wide menu, which produced
inconsistent-looking captions; this version was designed directly off a
real reference video's own caption typography (analyzed frame-by-frame),
then proven with real mockups on the user's own footage before building.

Position is also fixed by default - every beat anchors at the same
upper-safe-zone point (_DEFAULT_ANCHOR), above the subject's head, so a
caption plan needs zero manual positioning to look right out of the box.
A beat only breaks from that default when the user has dragged it in the
editor (anchor_x/anchor_y/size_scale set) - see captions.py's schema docs.

Rendering strategy (the ambitious choice, picked over ffmpeg's drawtext /
ASS subtitles for maximum creative freedom - real CSS, bundled webfonts,
smooth entrance animation):

  1. Build a small self-contained HTML/CSS/JS page that can render the
     WHOLE caption plan at any arbitrary time via a JS function,
     `window.setCaptionTime(t)`. This same page is also used for the live
     in-browser preview (Phase 8c) - one styling engine, two consumers.
     The three role fonts (Playfair Display Italic, Anton, Dancing Script)
     are bundled as local files under app/assets/fonts/ and embedded
     directly into the page as base64 data: URIs, so rendering never
     depends on network access or which fonts happen to be installed on
     this machine - unlike the old version's OS-only font stacks, these
     aren't fonts any OS ships by default.
  2. Drive that page with Playwright, stepping through every output frame's
     timestamp and screenshotting with a transparent background - so we get
     an image sequence of the caption layer alone, alpha channel intact.
  3. Feed that PNG sequence into ffmpeg as an `overlay` source on top of the
     existing reel, re-encoding once. Audio is copied straight from the
     base reel (captions never touch it).

This is a real new dependency (Playwright + a Chromium binary) - see
backend/README or .env.example for the one-time setup command.
"""
import base64
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Optional

from .ffmpeg_utils import FFmpegError, _run, probe

logger = logging.getLogger("reelmaker.caption_render")

# Caption frames are captured at a lower rate than the video itself - text
# motion reads fine well below 30fps, and halving/thirding the frame count
# is the single biggest lever on export time (each frame is a real browser
# screenshot, not a cheap operation).
CAPTION_FPS = 12
INTRO_SECONDS = 0.22  # how long a beat's entrance animation takes


class CaptionRenderError(RuntimeError):
    pass


def _launch_chromium(p):
    """
    Launches headless Chromium, tolerating environments where the installed
    browser revision doesn't match what this Playwright version expects by
    default (Playwright's default headless launch wants a separate
    "headless shell" build; some setups only have the full Chromium build
    installed under PLAYWRIGHT_BROWSERS_PATH). Falls back to that full build
    before giving up with the normal "please install chromium" error.
    """
    try:
        return p.chromium.launch()
    except Exception:
        alt = os.environ.get("PLAYWRIGHT_CHROMIUM_PATH")
        if not alt:
            base = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
            if base:
                candidate = Path(base) / "chromium"
                if candidate.exists():
                    alt = str(candidate)
        if alt and Path(alt).exists():
            return p.chromium.launch(executable_path=alt)
        raise


# --- Bundled webfonts --------------------------------------------------------
# Downloaded once from Google Fonts (OFL-licensed) and committed into the
# app rather than fetched at render time - a personal tool with no
# guaranteed internet access shouldn't depend on fonts.googleapis.com being
# reachable every time someone renders a caption.
_ASSETS_DIR = Path(__file__).parent / "assets" / "fonts"
_FONT_FILES = {
    "RM Setup Serif": _ASSETS_DIR / "PlayfairDisplay-Italic.ttf",
    "RM Punch Sans": _ASSETS_DIR / "Anton-Regular.ttf",
    "RM Accent Script": _ASSETS_DIR / "DancingScript.ttf",
}
_font_face_css_cache: Optional[str] = None


def _font_face_css() -> str:
    """Base64-embeds the three bundled font files as @font-face data: URIs, cached after the first call."""
    global _font_face_css_cache
    if _font_face_css_cache is not None:
        return _font_face_css_cache
    rules = []
    for family, path in _FONT_FILES.items():
        if not path.exists():
            logger.warning("Bundled font missing at %s - '%s' will fall back to a system font.", path, family)
            continue
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        rules.append(
            f"@font-face {{ font-family: '{family}'; "
            f"src: url(data:font/ttf;base64,{data}) format('truetype'); "
            f"font-weight: 100 900; font-style: normal; font-display: block; }}"
        )
    _font_face_css_cache = "\n".join(rules)
    return _font_face_css_cache


# --- Style engine: fixed per role, mirrors captions.py's beat schema --------
# The whole point of the redesign - no per-beat font/color/size choice, just
# these three looks. Sizes are a fraction of canvas width.
_ROLE_STYLE: dict[str, dict[str, str | float]] = {
    "setup": {
        "fontFamily": "'RM Setup Serif', Georgia, 'Times New Roman', serif",
        "fontWeight": "500",
        "sizeFrac": 0.050,
        "color": "#18140f",
        "letterSpacing": "0.005em",
    },
    "punch": {
        "fontFamily": "'RM Punch Sans', Impact, 'Arial Black', sans-serif",
        "fontWeight": "400",  # Anton is already a single heavy weight
        "sizeFrac": 0.100,
        "color": "#18140f",
        "letterSpacing": "-0.01em",
    },
    "accent": {
        "fontFamily": "'RM Accent Script', 'Segoe Script', cursive",
        "fontWeight": "700",
        "sizeFrac": 0.100,  # same tier as punch - it's replacing a punch-level word
        "color": "#2b45e6",
        "letterSpacing": "0em",
    },
}
# Every beat anchors here by default - a fixed upper-third safe zone above
# where a subject's head usually sits, centered horizontally. A beat only
# departs from this when the user has dragged it in the editor (see
# captions.py: anchor_x/anchor_y are a manual override the LLM never sets).
_DEFAULT_ANCHOR = {"x": 0.5, "y": 0.165}


def _beat_css(beat: dict[str, Any], canvas_w: int) -> dict[str, str]:
    role = beat.get("role") if beat.get("role") in _ROLE_STYLE else "punch"
    style_def = _ROLE_STYLE[role]
    # `size_scale` is a manual multiplier set only by the editor's on-canvas
    # resize handle (see captions.py) - never by the LLM, which never
    # controls size directly, only via role. None/absent means no override.
    size_scale = beat.get("size_scale") or 1.0
    size = round(canvas_w * float(style_def["sizeFrac"]) * size_scale)
    # `color` is an optional manual override (a 6-digit hex string, already
    # validated/lowercased by captions.py's _clamped_color) - falls back to
    # the role's fixed color when absent. Opacity is handled separately (see
    # build_caption_html's JS render()), since it has to combine with the
    # entrance-animation fade rather than being set as a static CSS value.
    color = beat.get("color") or str(style_def["color"])
    return {
        "fontFamily": str(style_def["fontFamily"]),
        "fontSize": f"{size}px",
        "fontWeight": str(style_def["fontWeight"]),
        "letterSpacing": str(style_def["letterSpacing"]),
        "color": color,
        "lineHeight": "1.15",
    }


def build_caption_html(beats: list[dict[str, Any]], canvas: dict[str, int]) -> str:
    """
    A self-contained HTML page exposing window.setCaptionTime(seconds).
    `#stage` is exactly canvas-sized with a transparent background - the
    only thing painted is the active beat's text.

    Per-beat CSS is computed here in Python (_beat_css) and embedded
    directly into each beat's JSON, rather than reimplementing the style
    engine a second time in JS - one source of truth for the mapping from a
    beat's role to actual styling.
    """
    cw, ch = canvas["width"], canvas["height"]
    beats_with_style = [{**b, "_style": _beat_css(b, cw)} for b in beats]
    beats_json = json.dumps(beats_with_style)
    default_anchor = json.dumps(_DEFAULT_ANCHOR)

    return f"""<!doctype html>
<html><head><meta charset="utf-8">
<style>
{_font_face_css()}
  html,body {{ margin:0; padding:0; background:transparent; overflow:hidden; }}
  #stage {{ position:relative; width:{cw}px; height:{ch}px; background:transparent; }}
  .beat {{ position:absolute; max-width:86%; width:max-content; left:50%; top:50%;
           display:flex; justify-content:center; text-align:center;
           will-change: opacity, transform; }}
  .beat .inner {{ display:inline-block; max-width:100%; min-width:0;
           white-space:normal; word-break:normal; overflow-wrap:normal;
           text-shadow: 0 2px 16px rgba(0,0,0,0.22); }}
</style></head>
<body>
<div id="stage"></div>
<script>
const BEATS = {beats_json};
const DEFAULT_ANCHOR = {default_anchor};
const CANVAS_W = {cw};
const CANVAS_H = {ch};

// A beat's on-screen anchor point: explicit anchor_x/anchor_y (set by
// dragging in the editor) if present, else the fixed default safe-zone
// point every beat uses out of the box - see captions.py's schema docs.
function anchorFor(beat) {{
  const ax = (beat.anchor_x !== null && beat.anchor_x !== undefined) ? beat.anchor_x : DEFAULT_ANCHOR.x;
  const ay = (beat.anchor_y !== null && beat.anchor_y !== undefined) ? beat.anchor_y : DEFAULT_ANCHOR.y;
  return {{ ax, ay }};
}}

function fitTextToWidth(inner) {{
  // Text wraps at spaces only (see .inner's word-break:normal) so a phrase
  // breaks cleanly between words - but any single word on its own line
  // (the whole text, or the widest word once wrapped) can still be wider
  // than the box, since a lone word can't wrap further. scrollWidth already
  // reflects that overflow even across wrapped lines, so just shrink until
  // it fits rather than letting a line spill or break mid-letter.
  const maxWidth = inner.parentElement.clientWidth;
  let size = parseFloat(getComputedStyle(inner).fontSize);
  const minSize = size * 0.45;
  let guard = 0;
  while (inner.scrollWidth > maxWidth && size > minSize && guard < 24) {{
    size -= 2;
    inner.style.fontSize = size + 'px';
    guard++;
  }}
}}

function computeIntro(beat, t) {{
  const dt = t - beat.start;
  const p = Math.max(0, Math.min(1, dt / {INTRO_SECONDS}));
  // ease-out cubic
  const eased = 1 - Math.pow(1 - p, 3);
  return {{ p: eased, raw: p }};
}}

function activeBeatAt(t) {{
  for (let i = 0; i < BEATS.length; i++) {{
    if (t >= BEATS[i].start && t < BEATS[i].end) return BEATS[i];
  }}
  // Between/after beats (rounding gaps) - hold the last beat that ended,
  // but only briefly (avoid captions lingering across a big silent gap).
  for (let i = BEATS.length - 1; i >= 0; i--) {{
    if (t >= BEATS[i].end) {{
      return (t - BEATS[i].end <= 0.5) ? BEATS[i] : null;
    }}
  }}
  return null;
}}

function render(t) {{
  const stage = document.getElementById('stage');
  stage.innerHTML = '';
  const beat = activeBeatAt(t);
  if (!beat) return;

  const div = document.createElement('div');
  div.className = 'beat';
  const {{ ax, ay }} = anchorFor(beat);
  const leftPx = ax * CANVAS_W;
  div.style.left = leftPx + 'px';
  div.style.top = (ay * CANVAS_H) + 'px';
  div.style.transform = 'translate(-50%, -50%)';

  // A center-anchored box can still sit close to an edge (a manually
  // dragged beat) - cap its width to the room actually available from its
  // anchor to the nearer edge, independent of the general 86%-of-canvas
  // cap, so it can't spill off-canvas.
  const availPx = 2 * Math.min(leftPx, CANVAS_W - leftPx);
  const marginPx = CANVAS_W * 0.04;
  div.style.maxWidth = Math.max(60, Math.min(CANVAS_W * 0.86, availPx - marginPx)) + 'px';

  const inner = document.createElement('span');
  inner.className = 'inner';
  inner.textContent = beat.text;
  Object.assign(inner.style, beat._style);
  div.appendChild(inner);
  stage.appendChild(div);
  fitTextToWidth(inner);

  const intro = computeIntro(beat, t);
  const translateY = (1 - intro.p) * 14;
  const scale = 0.92 + 0.08 * intro.p;
  // A beat's optional manual opacity override (captions.py's
  // _clamped_opacity, 0.1-1.0) multiplies into the entrance-animation fade
  // rather than being set as a flat CSS opacity - so a low-opacity caption
  // still fades in smoothly instead of just popping in at its final value.
  const baseOpacity = (beat.opacity !== null && beat.opacity !== undefined) ? beat.opacity : 1;
  inner.style.display = 'inline-block';
  inner.style.opacity = String(intro.p * baseOpacity);
  inner.style.transform = 'translateY(' + translateY + 'px) scale(' + scale + ')';
}}

window.setCaptionTime = render;
window.__captionReady = false;
// Wait for the bundled webfonts to actually finish loading before the first
// screenshot - otherwise early frames would capture a fallback font that
// then visibly swaps mid-clip once the real font loads.
document.fonts.ready.then(() => {{
  window.__captionReady = true;
  render(0);
}});
</script>
</body></html>"""


def render_caption_frames(
    beats: list[dict[str, Any]], canvas: dict[str, int], duration: float, frames_dir: Path, fps: int = CAPTION_FPS
) -> int:
    """
    Drives the HTML caption stage through every frame timestamp with
    Playwright, screenshotting each with a transparent background. Returns
    the number of frames written (frame_00000.png, frame_00001.png, ...).
    """
    import math

    frames_dir.mkdir(parents=True, exist_ok=True)
    html_path = frames_dir.parent / "caption_stage.html"
    html_path.write_text(build_caption_html(beats, canvas))

    n_frames = max(1, math.ceil(duration * fps) + 1)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise CaptionRenderError(
            "The 'playwright' package isn't installed. In backend/ (venv active), run: "
            "pip install playwright && playwright install chromium"
        ) from e

    try:
        with sync_playwright() as p:
            browser = _launch_chromium(p)
            try:
                page = browser.new_page(
                    viewport={"width": canvas["width"], "height": canvas["height"]},
                    device_scale_factor=1,
                )
                page.goto(f"file://{html_path}")
                page.wait_for_function("window.__captionReady === true", timeout=10000)
                for i in range(n_frames):
                    t = i / fps
                    page.evaluate("(t) => window.setCaptionTime(t)", t)
                    out = frames_dir / f"frame_{i:05d}.png"
                    page.screenshot(path=str(out), omit_background=True)
            finally:
                browser.close()
    except CaptionRenderError:
        raise
    except Exception as e:  # noqa: BLE001 - surface any Playwright/browser failure clearly
        raise CaptionRenderError(f"Caption rendering (headless browser) failed: {e}") from e
    finally:
        html_path.unlink(missing_ok=True)

    return n_frames


def composite_captions(base_video_path: Path, frames_dir: Path, fps: int, out_path: Path) -> None:
    """Overlays the transparent caption PNG sequence onto the base video, keeping its audio untouched."""
    cmd = [
        "ffmpeg", "-y",
        "-i", str(base_video_path),
        "-framerate", str(fps), "-i", str(frames_dir / "frame_%05d.png"),
        "-filter_complex", "[0:v][1:v]overlay=0:0:format=auto[vout]",
        "-map", "[vout]",
        "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-shortest",
        "-movflags", "+faststart",
        str(out_path),
    ]
    try:
        _run(cmd)
    except FFmpegError as e:
        raise CaptionRenderError(f"Compositing captions onto the video failed: {e}") from e


def render_captioned_reel(base_reel_path: Path, beats: list[dict[str, Any]], canvas: dict[str, int], out_path: Path) -> None:
    """Top-level entry point: base_reel_path (already 9:16) -> out_path (same, with burned-in captions)."""
    info = probe(base_reel_path)
    duration = info["duration_seconds"]

    with tempfile.TemporaryDirectory() as tmp:
        frames_dir = Path(tmp) / "frames"
        render_caption_frames(beats, canvas, duration, frames_dir, fps=CAPTION_FPS)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        composite_captions(base_reel_path, frames_dir, CAPTION_FPS, out_path)
