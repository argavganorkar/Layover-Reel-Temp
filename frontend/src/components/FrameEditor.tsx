"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  Clip,
  FrameBox,
  FrameLayout,
  FramePosition,
  clipDownloadUrl,
  fetchFramePlan,
  reelDownloadUrl,
  startReframe,
} from "@/lib/api";

// Fixed output canvas for Phase 6 - 9:16 vertical, matches the backend
// default. A later phase can expose alternate canvas sizes if needed.
const CANVAS_W = 1080;
const CANVAS_H = 1920;

const MIN_BOX_HEIGHT = 0.15;
// Shortest a single timed position is allowed to be - stops "split here"
// from carving off a sliver too thin to be a meaningful framing choice.
const MIN_POSITION_SECONDS = 0.75;

const PREVIEW_W = 160;
const PREVIEW_H = Math.round((PREVIEW_W * CANVAS_H) / CANVAS_W);

// How many boxes each layout preset uses, and whether it crops at all -
// "horizontal" letterboxes the whole frame with no cropping, matching the
// reference tool's "Horizontal" option. Split/trio stack that many boxes
// (via the backend's vstack), vertical/centered/spotlight are a single
// cropped box at different default zoom levels. "free" is also a single
// box, but - like horizontal - it's never cropped: it's a user-chosen
// region of the frame (any shape/position) that gets letterboxed (fit in
// full, black bars added wherever it doesn't fill 9:16) rather than
// cropped to fill.
const LAYOUT_BOX_COUNT: Record<FrameLayout, number> = {
  vertical: 1,
  free: 1,
  centered: 1,
  spotlight: 1,
  split: 2,
  trio: 3,
  horizontal: 0,
};
const LAYOUT_SCALE: Partial<Record<FrameLayout, number>> = {
  vertical: 1.0,
  centered: 0.8,
  spotlight: 0.55,
};
const LAYOUT_LABELS: { value: FrameLayout; label: string }[] = [
  { value: "vertical", label: "Vertical" },
  { value: "free", label: "Free" },
  { value: "centered", label: "Centered" },
  { value: "spotlight", label: "Spotlight" },
  { value: "split", label: "Split" },
  { value: "trio", label: "Trio" },
  { value: "horizontal", label: "Horizontal" },
];
const BOX_COLORS = ["border-emerald-400 bg-emerald-400/10", "border-sky-400 bg-sky-400/10", "border-amber-400 bg-amber-400/10"];
const BOX_HANDLE_COLORS = ["bg-emerald-500", "bg-sky-500", "bg-amber-500"];
// Cycled per timeline position so adjacent segments are visually distinct.
const POSITION_COLORS = ["bg-blue-400", "bg-teal-400", "bg-rose-400", "bg-amber-400", "bg-violet-400"];

// The aspect ratio a single box must be locked to so what's shown while
// dragging is exactly what renders, with no surprise "cover" cropping: each
// box fills a 1/n vertical slice of the canvas, so its target ratio is the
// canvas width against a 1/n share of the canvas height.
function targetArForCount(n: number): number {
  return CANVAS_W / (CANVAS_H / Math.max(1, n));
}

// A box's normalized x/y/width/height are fractions of the SOURCE clip's
// own frame, which usually isn't square (e.g. 16:9) - so keeping a box's
// true pixel aspect ratio locked to its target slot requires knowing the
// source resolution too. Naively working in normalized space alone would
// silently distort the box on any non-square source.
//
// Width can't just be capped at the literal max of 1: since height is
// derived from width via the target aspect ratio (height = width * sourceW
// / (targetAr * sourceH)), a width of 1 often implies a height > 1 - taller
// than the source frame itself - which is exactly what happened when
// resizing a Split/Trio box past a certain point (a 400 from the backend's
// "box falls outside the source frame" check). The real ceiling is whatever
// width makes height == 1, i.e. sourceH * targetAr / sourceW.
//
// `targetAr === null` means "free" mode: no locked aspect ratio at all -
// width and height are clamped independently to whatever the user drags.
// The backend still cover-crops the result to fill 9:16 exactly (same as
// it already does for Split/Trio boxes that don't perfectly match their
// slice's aspect ratio), so an odd-shaped free box never distorts the
// output - it just means not every pixel of the drawn box survives.
function clampBox(b: FrameBox, sourceW: number, sourceH: number, targetAr: number | null): FrameBox {
  if (targetAr === null) {
    const width = Math.min(1, Math.max(MIN_BOX_HEIGHT, b.width));
    const height = Math.min(1, Math.max(MIN_BOX_HEIGHT, b.height));
    const x = Math.min(1 - width, Math.max(0, b.x));
    const y = Math.min(1 - height, Math.max(0, b.y));
    return { x: round4(x), y: round4(y), width: round4(width), height: round4(height) };
  }
  const minWidth = (MIN_BOX_HEIGHT * sourceH * targetAr) / sourceW;
  const maxWidth = Math.min(1, (sourceH * targetAr) / sourceW);
  const width = Math.min(maxWidth, Math.max(minWidth, b.width));
  const height = (width * sourceW) / (targetAr * sourceH);
  const x = Math.min(1 - width, Math.max(0, b.x));
  const y = Math.min(1 - height, Math.max(0, b.y));
  return { x: round4(x), y: round4(y), width: round4(width), height: round4(height) };
}

// Given a (possibly odd-shaped) box, what the backend's "cover" crop will
// actually keep once it scales the box to fill a `destAr`-shaped canvas -
// centered crop off whichever axis is oversized, no distortion. Used to
// draw the live 9:16 preview accurately for crop-mode boxes (vertical/
// centered/spotlight/split/trio - normally already at the target aspect
// ratio by construction, but this keeps the preview honest even so). Not
// used for "free", which letterboxes (contain-fits) instead of cropping,
// so nothing the user selects there is ever cropped away.
function coverInset(box: FrameBox, sourceW: number, sourceH: number, destAr: number): FrameBox {
  const bwPx = box.width * sourceW;
  const bhPx = box.height * sourceH;
  const boxAr = bwPx / bhPx;
  if (!Number.isFinite(boxAr) || Math.abs(boxAr - destAr) < 0.01) return box;
  if (boxAr > destAr) {
    const w = (bhPx * destAr) / sourceW;
    return { x: round4(box.x + (box.width - w) / 2), y: box.y, width: round4(w), height: box.height };
  }
  const h = bwPx / destAr / sourceH;
  return { x: box.x, y: round4(box.y + (box.height - h) / 2), width: box.width, height: round4(h) };
}

// Default box for slot `i` of `n` equal horizontal slices of the source
// frame (e.g. for split/trio, a reasonable starting guess is "each person
// occupies their own share of the frame width") - the user drags from
// there to actually center it on whoever they want.
function defaultBoxForSlot(
  sourceW: number,
  sourceH: number,
  targetAr: number,
  slotIndex: number,
  totalSlots: number,
  scale = 1.0
): FrameBox {
  const sliceW = sourceW / totalSlots;
  const originX = slotIndex * sliceW;
  const sliceAr = sliceW / sourceH;

  let boxWpx: number;
  let boxHpx: number;
  if (sliceAr > targetAr) {
    boxHpx = sourceH * scale;
    boxWpx = boxHpx * targetAr;
  } else {
    boxWpx = sliceW * scale;
    boxHpx = boxWpx / targetAr;
  }

  const localX = (sliceW - boxWpx) / 2;
  const y = (sourceH - boxHpx) / 2;
  return {
    x: round4((originX + localX) / sourceW),
    y: round4(y / sourceH),
    width: round4(boxWpx / sourceW),
    height: round4(boxHpx / sourceH),
  };
}

function defaultPositionForLayout(
  layout: FrameLayout,
  sourceW: number,
  sourceH: number,
  start: number,
  end: number
): FramePosition {
  const n = LAYOUT_BOX_COUNT[layout];
  if (n === 0) {
    return { start, end, layout, mode: "letterbox", boxes: [] };
  }
  if (layout === "free") {
    // Start from the whole frame, exactly like "Horizontal" shows (and
    // renders identically until reshaped) - the user shrinks/repositions/
    // reshapes from there. Letterbox, not crop: whatever region ends up
    // selected is shown in full, padded with black wherever it doesn't
    // fill the 9:16 canvas - nothing selected is ever cropped away.
    return { start, end, layout, mode: "letterbox", boxes: [{ x: 0, y: 0, width: 1, height: 1 }] };
  }
  const targetAr = targetArForCount(n);
  const scale = LAYOUT_SCALE[layout] ?? 1.0;
  const boxes = Array.from({ length: n }, (_, i) => defaultBoxForSlot(sourceW, sourceH, targetAr, i, n, scale));
  return { start, end, layout, mode: "crop", boxes };
}

function round4(n: number): number {
  return Math.round(n * 10000) / 10000;
}
function round2(n: number): number {
  return Math.round(n * 100) / 100;
}
function formatClock(totalSeconds: number): string {
  const s = Math.max(0, totalSeconds);
  const m = Math.floor(s / 60);
  const sec = (s % 60).toFixed(1);
  return `${m}:${sec.padStart(4, "0")}`;
}

type BoxDragState = { boxIndex: number; mode: "move" | "resize"; startX: number; startY: number; startBox: FrameBox };
type BoundaryDragState = { leftIndex: number; startX: number; startBoundary: number };

export default function FrameEditor({
  jobId,
  index,
  clip,
  onClose,
  onUpdate,
}: {
  jobId: string;
  index: number;
  clip: Clip;
  onClose: () => void;
  onUpdate: (clip: Clip) => void;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const frameRef = useRef<HTMLDivElement>(null);
  const timelineRef = useRef<HTMLDivElement>(null);
  const boxDragRef = useRef<BoxDragState | null>(null);
  const boundaryDragRef = useRef<BoundaryDragState | null>(null);

  // The full frame plan is a list of time-ranged "positions" - most clips
  // have exactly one (spanning the whole clip), but a position can be split
  // so the framing changes partway through (e.g. pan from one speaker to
  // another). Whichever position contains the video's current playback
  // time is the "active" one: its boxes are what's shown draggable over the
  // video and what the small 9:16 preview reflects, so scrubbing across a
  // split boundary visibly swaps the framing being edited/previewed - the
  // same thing that will happen in the final render.
  const [positions, setPositions] = useState<FramePosition[] | null>(null);
  const [sourceDims, setSourceDims] = useState<{ width: number; height: number } | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [playheadTime, setPlayheadTime] = useState(0);

  useEffect(() => {
    fetchFramePlan(jobId, index)
      .then(({ frame_plan, source_width, source_height }) => {
        setPositions(frame_plan.positions);
        setSourceDims({ width: source_width, height: source_height });
      })
      .catch((e) => setLoadError(e instanceof Error ? e.message : "Could not load frame plan."))
      .finally(() => setLoading(false));
  }, [jobId, index]);

  const clipDuration = positions && positions.length ? positions[positions.length - 1].end : 0;

  const activeIndex = useMemo(() => {
    if (!positions || positions.length === 0) return 0;
    const idx = positions.findIndex((p) => playheadTime >= p.start && playheadTime < p.end);
    return idx === -1 ? positions.length - 1 : idx;
  }, [positions, playheadTime]);

  const activePosition = positions ? positions[activeIndex] : null;

  // Moving playhead - drives both which position is "active" and the red
  // marker on the timeline bar. rAF (not just `timeupdate`) so it moves
  // smoothly during playback; the seeked/pause listeners catch scrubs that
  // never trigger a "playing" state at all.
  useEffect(() => {
    let raf: number;
    const tick = () => {
      const v = videoRef.current;
      if (v && !v.paused && !v.seeking) setPlayheadTime(v.currentTime);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);

  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    const sync = () => setPlayheadTime(v.currentTime);
    v.addEventListener("seeked", sync);
    v.addEventListener("pause", sync);
    v.addEventListener("loadedmetadata", sync);
    return () => {
      v.removeEventListener("seeked", sync);
      v.removeEventListener("pause", sync);
      v.removeEventListener("loadedmetadata", sync);
    };
  }, []);

  // Live 9:16 preview: for a crop layout, draw each box's cropped region
  // into its stacked slice of the canvas (mirroring the backend's vstack);
  // for letterbox, fit the whole frame with black bars. Always reflects the
  // ACTIVE position (wherever the playhead is), so it updates live as
  // playback crosses from one timed position into the next.
  useEffect(() => {
    let raf: number;
    const draw = () => {
      const video = videoRef.current;
      const canvas = canvasRef.current;
      if (video && canvas && activePosition && video.videoWidth) {
        const ctx = canvas.getContext("2d");
        if (ctx) {
          ctx.clearRect(0, 0, PREVIEW_W, PREVIEW_H);
          if (activePosition.mode === "letterbox") {
            const box = activePosition.boxes[0];
            // Whole frame ("Horizontal") or a selected region ("Free") -
            // either way, contain-fit it whole with black bars, never crop.
            const bx = box ? box.x * video.videoWidth : 0;
            const by = box ? box.y * video.videoHeight : 0;
            const bw = box ? box.width * video.videoWidth : video.videoWidth;
            const bh = box ? box.height * video.videoHeight : video.videoHeight;
            const scale = Math.min(PREVIEW_W / bw, PREVIEW_H / bh);
            const w = bw * scale;
            const h = bh * scale;
            ctx.fillStyle = "black";
            ctx.fillRect(0, 0, PREVIEW_W, PREVIEW_H);
            ctx.drawImage(video, bx, by, bw, bh, (PREVIEW_W - w) / 2, (PREVIEW_H - h) / 2, w, h);
          } else {
            const n = activePosition.boxes.length;
            const sliceH = Math.floor(PREVIEW_H / n);
            activePosition.boxes.forEach((box, i) => {
              const h = i === n - 1 ? PREVIEW_H - sliceH * (n - 1) : sliceH;
              // Cover-crop (not stretch) so a box that isn't already at its
              // slice's exact aspect ratio previews exactly like the
              // backend will render it: center-cropped to fill, never
              // distorted. Normally a no-op here since these boxes are
              // aspect-locked by construction, but keeps the preview
              // honest regardless.
              const inset = coverInset(box, video.videoWidth, video.videoHeight, PREVIEW_W / h);
              ctx.drawImage(
                video,
                inset.x * video.videoWidth,
                inset.y * video.videoHeight,
                inset.width * video.videoWidth,
                inset.height * video.videoHeight,
                0,
                i * sliceH,
                PREVIEW_W,
                h
              );
            });
          }
        }
      }
      raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, [activePosition]);

  function updateActivePosition(updater: (p: FramePosition) => FramePosition) {
    setPositions((prev) => {
      if (!prev) return prev;
      return prev.map((p, i) => (i === activeIndex ? updater(p) : p));
    });
  }

  function updateBox(boxIndex: number, box: FrameBox) {
    updateActivePosition((p) => ({ ...p, boxes: p.boxes.map((b, i) => (i === boxIndex ? box : b)) }));
  }

  function handleLayoutChange(layout: FrameLayout) {
    if (!sourceDims || !activePosition) return;
    updateActivePosition((p) => ({
      ...defaultPositionForLayout(layout, sourceDims.width, sourceDims.height, p.start, p.end),
    }));
  }

  function seekTo(t: number) {
    const v = videoRef.current;
    if (v) v.currentTime = Math.max(0, Math.min(clipDuration - 0.01, t));
    setPlayheadTime(t);
  }

  function handleSelectPosition(idx: number) {
    if (!positions) return;
    const v = videoRef.current;
    if (v) v.pause();
    // Seek just inside the segment so the displayed frame and the overlay
    // boxes being edited actually correspond to each other. Used by the
    // compact position pills below the bar, where "give me position N" is
    // the whole point.
    seekTo(positions[idx].start + 0.01);
  }

  // Clicking anywhere on the timeline bar itself scrubs to the exact point
  // clicked (not just the start of whichever segment happens to be under
  // the cursor) - this is how the user actually picks a spot to split at,
  // since every pixel of the bar is covered by some position's button.
  function handleTimelineScrub(e: React.MouseEvent) {
    const bar = timelineRef.current;
    if (!bar) return;
    const rect = bar.getBoundingClientRect();
    const frac = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
    videoRef.current?.pause();
    seekTo(frac * clipDuration);
  }

  function handleSplitHere() {
    if (!positions || !activePosition) return;
    const t = round2(playheadTime);
    if (t - activePosition.start < MIN_POSITION_SECONDS || activePosition.end - t < MIN_POSITION_SECONDS) return;
    const first = { ...activePosition, end: t };
    const second = { ...activePosition, start: t };
    setPositions([...positions.slice(0, activeIndex), first, second, ...positions.slice(activeIndex + 1)]);
  }

  function handleDeleteActive() {
    if (!positions || positions.length <= 1) return;
    const idx = activeIndex;
    let mergedBoundary: number;
    const next = (() => {
      if (idx === 0) {
        mergedBoundary = positions[0].start;
        const merged = { ...positions[1], start: positions[0].start };
        return [merged, ...positions.slice(2)];
      }
      mergedBoundary = positions[idx - 1].start + 0.01;
      const merged = [...positions];
      merged[idx - 1] = { ...merged[idx - 1], end: positions[idx].end };
      merged.splice(idx, 1);
      return merged;
    })();
    setPositions(next);
    seekTo(mergedBoundary!);
  }

  function handleBoxPointerDown(e: React.PointerEvent, boxIndex: number) {
    if (!activePosition) return;
    videoRef.current?.pause();
    (e.target as Element).setPointerCapture(e.pointerId);
    boxDragRef.current = { boxIndex, mode: "move", startX: e.clientX, startY: e.clientY, startBox: activePosition.boxes[boxIndex] };
  }

  function handleHandlePointerDown(e: React.PointerEvent, boxIndex: number) {
    if (!activePosition) return;
    videoRef.current?.pause();
    e.stopPropagation();
    (e.target as Element).setPointerCapture(e.pointerId);
    boxDragRef.current = { boxIndex, mode: "resize", startX: e.clientX, startY: e.clientY, startBox: activePosition.boxes[boxIndex] };
  }

  function handleFramePointerMove(e: React.PointerEvent) {
    const drag = boxDragRef.current;
    const frameEl = frameRef.current;
    if (!drag || !frameEl || !sourceDims || !activePosition) return;
    const rect = frameEl.getBoundingClientRect();
    const dx = (e.clientX - drag.startX) / rect.width;
    const dy = (e.clientY - drag.startY) / rect.height;
    // "Free" has no locked aspect ratio - width and height drag independently.
    const isFree = activePosition.layout === "free";
    const targetAr = isFree ? null : targetArForCount(activePosition.boxes.length);

    if (drag.mode === "move") {
      updateBox(
        drag.boxIndex,
        clampBox({ ...drag.startBox, x: drag.startBox.x + dx, y: drag.startBox.y + dy }, sourceDims.width, sourceDims.height, targetAr)
      );
    } else if (isFree) {
      const newWidth = drag.startBox.width + dx;
      const newHeight = drag.startBox.height + dy;
      updateBox(drag.boxIndex, clampBox({ ...drag.startBox, width: newWidth, height: newHeight }, sourceDims.width, sourceDims.height, null));
    } else {
      const newHeight = drag.startBox.height + dy;
      const newWidth = (newHeight * targetAr! * sourceDims.height) / sourceDims.width;
      updateBox(drag.boxIndex, clampBox({ ...drag.startBox, width: newWidth }, sourceDims.width, sourceDims.height, targetAr));
    }
  }

  function handleFramePointerUp() {
    boxDragRef.current = null;
  }

  // --- Timeline: click a segment to select it, drag the divider between
  // two segments to move their shared boundary, split/delete buttons act on
  // whichever position is currently active. ---

  function handleBoundaryPointerDown(e: React.PointerEvent, leftIndex: number) {
    if (!positions) return;
    e.stopPropagation();
    (e.target as Element).setPointerCapture(e.pointerId);
    boundaryDragRef.current = { leftIndex, startX: e.clientX, startBoundary: positions[leftIndex].end };
  }

  function handleTimelinePointerMove(e: React.PointerEvent) {
    const drag = boundaryDragRef.current;
    const bar = timelineRef.current;
    if (!drag || !bar || !positions) return;
    const rect = bar.getBoundingClientRect();
    const dx = (e.clientX - drag.startX) / rect.width;
    const newBoundary = round2(drag.startBoundary + dx * clipDuration);
    const left = positions[drag.leftIndex];
    const right = positions[drag.leftIndex + 1];
    if (!right) return;
    const clamped = Math.min(right.end - MIN_POSITION_SECONDS, Math.max(left.start + MIN_POSITION_SECONDS, newBoundary));
    const next = [...positions];
    next[drag.leftIndex] = { ...left, end: clamped };
    next[drag.leftIndex + 1] = { ...right, start: clamped };
    setPositions(next);
  }

  function handleTimelinePointerUp() {
    boundaryDragRef.current = null;
  }

  async function handleSave() {
    if (!positions) return;
    setSaving(true);
    setSaveError(null);
    try {
      const updatedClip = await startReframe(jobId, index, positions, { width: CANVAS_W, height: CANVAS_H });
      onUpdate(updatedClip);
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : "Could not start reframing.");
    } finally {
      setSaving(false);
    }
  }

  const isRendering = clip.reframe_status === "pending" || clip.reframe_status === "running";
  const canSplit =
    !!activePosition && playheadTime - activePosition.start >= MIN_POSITION_SECONDS && activePosition.end - playheadTime >= MIN_POSITION_SECONDS;

  return (
    <div className="mt-2 rounded-xl border border-border bg-surface p-4">
      <div className="flex items-center justify-between">
        <p className="text-xs font-medium text-ink">Frame &ldquo;{clip.title}&rdquo; for 9:16</p>
        <button onClick={onClose} className="text-xs text-ink-faint hover:text-ink-muted">
          Close
        </button>
      </div>

      {loading && <p className="mt-2 text-xs text-ink-muted">Loading…</p>}
      {loadError && <p className="mt-2 text-xs text-error">{loadError}</p>}

      {!loading && !loadError && positions && activePosition && sourceDims && (
        <div className="mt-2 space-y-2">
          <div className="flex flex-wrap gap-1.5">
            {LAYOUT_LABELS.map((l) => (
              <button
                key={l.value}
                onClick={() => handleLayoutChange(l.value)}
                className={`rounded-md px-2.5 py-1 text-xs font-medium ${
                  activePosition.layout === l.value
                    ? "bg-accent-solid text-[#faf6f0]"
                    : "bg-surface-elevated text-ink-muted hover:bg-surface-hover"
                }`}
              >
                {l.label}
              </button>
            ))}
          </div>

          <div className="flex flex-wrap gap-4">
            <div className="min-w-[220px] flex-1">
              <p className="mb-1 text-xs text-ink-muted">
                {activePosition.layout === "free"
                  ? "Free - drag to reposition, drag the corner to resize to any shape. Nothing you select gets cropped - the selected region is always shown in full, with black bars added top/bottom or left/right wherever it doesn't fill 9:16."
                  : activePosition.mode === "letterbox"
                    ? "Horizontal shows the whole frame, letterboxed - nothing to drag."
                    : activePosition.boxes.length === 1
                      ? "Drag the box to reposition, drag the corner handle to resize."
                      : "Each colored box becomes its own stacked band, top to bottom. Drag to reposition, drag a corner to resize."}
              </p>
              <div
                ref={frameRef}
                className="relative select-none"
                style={{ touchAction: "none" }}
                onPointerMove={handleFramePointerMove}
                onPointerUp={handleFramePointerUp}
                onPointerCancel={handleFramePointerUp}
              >
                <video
                  ref={videoRef}
                  src={clipDownloadUrl(jobId, index)}
                  controls
                  preload="auto"
                  crossOrigin="anonymous"
                  className="w-full rounded-md bg-black"
                  onLoadedData={(e) => {
                    // Browsers often leave a paused, never-played video showing
                    // nothing (black) until a frame is actually decoded. A
                    // tiny forced seek makes it paint immediately, so the crop
                    // box(es) and 9:16 preview aren't blank before hitting play.
                    const v = e.currentTarget;
                    if (v.currentTime === 0) v.currentTime = 0.01;
                  }}
                />
                {/* A box is draggable whenever one exists - true for every
                    "crop" position, and for "letterbox" only when it's a
                    "free" selection (plain "horizontal" has no boxes at
                    all, so nothing renders here for it). */}
                {activePosition.boxes.map((box, i) => (
                  <div
                    key={i}
                    onPointerDown={(e) => handleBoxPointerDown(e, i)}
                    className={`absolute cursor-move border-2 ${BOX_COLORS[i % BOX_COLORS.length]}`}
                    style={{
                      left: `${box.x * 100}%`,
                      top: `${box.y * 100}%`,
                      width: `${box.width * 100}%`,
                      height: `${box.height * 100}%`,
                    }}
                  >
                    {activePosition.boxes.length > 1 && (
                      <span className="absolute left-1 top-1 rounded bg-black/60 px-1 text-[10px] text-white">
                        {i + 1}
                      </span>
                    )}
                    <div
                      onPointerDown={(e) => handleHandlePointerDown(e, i)}
                      className={`absolute -bottom-2 -right-2 h-4 w-4 cursor-nwse-resize rounded-full border-2 border-white shadow ${BOX_HANDLE_COLORS[i % BOX_HANDLE_COLORS.length]}`}
                    />
                  </div>
                ))}
              </div>
            </div>

            <div className="shrink-0">
              <p className="mb-1 text-xs text-ink-muted">Preview (9:16)</p>
              <canvas
                ref={canvasRef}
                width={PREVIEW_W}
                height={PREVIEW_H}
                className="rounded-md bg-black"
                style={{ width: PREVIEW_W, height: PREVIEW_H }}
              />

              <div className="mt-2">
                {!isRendering && clip.reframe_status !== "done" && (
                  <button
                    onClick={handleSave}
                    disabled={saving}
                    className="rounded-md bg-accent-solid px-3 py-1.5 text-xs font-medium text-[#faf6f0] hover:bg-accent-solid-hover disabled:opacity-50"
                  >
                    {saving ? "Starting…" : "Render 9:16"}
                  </button>
                )}
                {isRendering && (
                  <p className="flex items-center gap-1.5 text-xs text-accent">
                    <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent" />
                    Rendering…
                  </p>
                )}
                {clip.reframe_status === "error" && (
                  <p className="mt-1 text-xs text-error">{clip.reframe_error}</p>
                )}
                {clip.reframe_status === "done" && (
                  <div className="space-y-1.5">
                    <a
                      href={reelDownloadUrl(jobId, index)}
                      download
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-block rounded-md bg-success/10 px-2.5 py-1 text-xs font-medium text-success hover:bg-success/20"
                    >
                      Download 9:16 reel ↓
                    </a>
                    <button
                      onClick={handleSave}
                      disabled={saving}
                      className="block rounded-md bg-surface-elevated px-2.5 py-1 text-xs font-medium text-ink shadow-warm hover:bg-surface-hover disabled:opacity-50"
                    >
                      {saving ? "Starting…" : "↻ Re-render with current framing"}
                    </button>
                  </div>
                )}
                {saveError && <p className="mt-1 text-xs text-error">{saveError}</p>}
              </div>
            </div>
          </div>

          {/* --- Timeline: pan from one framing to another partway through --- */}
          <div className="border-t border-border pt-2">
            <div className="flex items-center justify-between">
              <p className="text-xs text-ink-muted">
                Timeline - split to change the framing partway through this clip.
              </p>
              <div className="flex gap-1.5">
                <button
                  onClick={handleSplitHere}
                  disabled={!canSplit}
                  className="rounded-md bg-surface-elevated px-2 py-1 text-[11px] font-medium text-ink hover:bg-surface-hover disabled:opacity-40"
                  title={canSplit ? "Split this position at the playhead" : "Move the playhead away from an edge to split here"}
                >
                  ✂ Split here
                </button>
                <button
                  onClick={handleDeleteActive}
                  disabled={positions.length <= 1}
                  className="rounded-md bg-surface-elevated px-2 py-1 text-[11px] font-medium text-ink hover:bg-surface-hover disabled:opacity-40"
                  title="Remove this position, merging it into its neighbor"
                >
                  🗑 Remove position
                </button>
              </div>
            </div>

            <div
              ref={timelineRef}
              className="relative mt-1.5 h-9 select-none overflow-hidden rounded"
              style={{ touchAction: "none" }}
              onPointerMove={handleTimelinePointerMove}
              onPointerUp={handleTimelinePointerUp}
              onPointerCancel={handleTimelinePointerUp}
            >
              {positions.map((p, i) => {
                const leftPct = (p.start / clipDuration) * 100;
                const widthPct = ((p.end - p.start) / clipDuration) * 100;
                return (
                  <button
                    key={i}
                    onClick={handleTimelineScrub}
                    className={`absolute top-0 h-full border-r border-white/40 text-[10px] font-medium text-white last:border-r-0 ${
                      POSITION_COLORS[i % POSITION_COLORS.length]
                    } ${i === activeIndex ? "ring-2 ring-inset ring-white" : "opacity-70 hover:opacity-90"}`}
                    style={{ left: `${leftPct}%`, width: `${widthPct}%` }}
                    title={`${formatClock(p.start)} - ${formatClock(p.end)} (${p.layout}) - click to scrub here`}
                  >
                    <span className="block truncate px-1 pt-1">{p.layout}</span>
                  </button>
                );
              })}
              {/* Draggable boundaries between adjacent positions */}
              {positions.slice(0, -1).map((p, i) => (
                <div
                  key={`b${i}`}
                  onPointerDown={(e) => handleBoundaryPointerDown(e, i)}
                  className="absolute top-0 z-10 h-full w-2 -translate-x-1/2 cursor-ew-resize"
                  style={{ left: `${(p.end / clipDuration) * 100}%` }}
                />
              ))}
              {/* Playhead */}
              <div
                className="pointer-events-none absolute top-0 h-full w-[2px] bg-red-500"
                style={{ left: `${Math.min(100, Math.max(0, (playheadTime / clipDuration) * 100))}%` }}
              />
            </div>
            <div className="mt-1 flex justify-between text-[10px] text-ink-faint">
              <span>0:00.0</span>
              <span>{formatClock(clipDuration)}</span>
            </div>

            <div className="mt-1.5 flex flex-wrap gap-1 text-[11px] text-ink-muted">
              {positions.map((p, i) => (
                <button
                  key={i}
                  onClick={() => handleSelectPosition(i)}
                  className={`rounded px-1.5 py-0.5 ${
                    i === activeIndex ? "bg-accent/15 font-medium text-accent" : "bg-surface-elevated hover:bg-surface-hover"
                  }`}
                >
                  {formatClock(p.start)}–{formatClock(p.end)} · {p.layout}
                </button>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
