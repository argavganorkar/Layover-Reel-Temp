"use client";

import { useEffect, useRef, useState } from "react";
import { Clip, sourceFileUrl, trimClip } from "@/lib/api";

// How much extra room around the clip's current boundaries to show on the
// trim bar, so there's something to drag into on both sides. Scales with
// clip length (a 6s clip and a 90s clip want different amounts of "room to
// extend"), clamped to a sane range - re-opening the editor after applying
// a change recenters this around the new boundaries, so extending further
// is just "adjust, apply, adjust again" rather than needing one giant range.
const MIN_PAD = 8;
const MAX_PAD = 45;

function formatClock(totalSeconds: number): string {
  const s = Math.max(0, totalSeconds);
  const m = Math.floor(s / 60);
  const sec = (s % 60).toFixed(1);
  return `${m}:${sec.padStart(4, "0")}`;
}

// Parses "m:ss", "m:ss.s", or a bare number of seconds back into seconds.
function parseClock(text: string): number | null {
  const trimmed = text.trim();
  if (!trimmed) return null;
  const colonMatch = trimmed.match(/^(\d+):(\d+(?:\.\d+)?)$/);
  if (colonMatch) {
    const m = parseInt(colonMatch[1], 10);
    const s = parseFloat(colonMatch[2]);
    return m * 60 + s;
  }
  const bare = parseFloat(trimmed);
  return Number.isFinite(bare) ? bare : null;
}

type DragTarget = "start" | "end" | null;

export default function TrimEditor({
  jobId,
  index,
  clip,
  sourceDuration,
  onClose,
  onUpdate,
}: {
  jobId: string;
  index: number;
  clip: Clip;
  sourceDuration: number;
  onClose: () => void;
  onUpdate: (clip: Clip) => void;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const barRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<DragTarget>(null);

  const pad = Math.min(MAX_PAD, Math.max(MIN_PAD, (clip.end - clip.start) * 0.6));
  const [windowStart, setWindowStart] = useState(() => Math.max(0, clip.start - pad));
  const [windowEnd, setWindowEnd] = useState(() => Math.min(sourceDuration, clip.end + pad));
  const [trimStart, setTrimStart] = useState(clip.start);
  const [trimEnd, setTrimEnd] = useState(clip.end);
  const [startText, setStartText] = useState(formatClock(clip.start));
  const [endText, setEndText] = useState(formatClock(clip.end));

  const [applying, setApplying] = useState(false);
  const [applyError, setApplyError] = useState<string | null>(null);
  const [playingPreview, setPlayingPreview] = useState(false);
  const [playheadTime, setPlayheadTime] = useState<number | null>(null);

  // Keep the widened window in sync if a typed time lands outside it.
  function ensureWindowIncludes(t: number) {
    setWindowStart((w) => Math.max(0, Math.min(w, t - 2)));
    setWindowEnd((w) => Math.min(sourceDuration, Math.max(w, t + 2)));
  }

  function setStart(t: number, seek = true) {
    const clamped = Math.max(0, Math.min(t, trimEnd - 0.2));
    setTrimStart(clamped);
    setStartText(formatClock(clamped));
    ensureWindowIncludes(clamped);
    if (seek && videoRef.current) videoRef.current.currentTime = clamped;
  }

  function setEnd(t: number, seek = true) {
    const clamped = Math.min(sourceDuration, Math.max(t, trimStart + 0.2));
    setTrimEnd(clamped);
    setEndText(formatClock(clamped));
    ensureWindowIncludes(clamped);
    if (seek && videoRef.current) videoRef.current.currentTime = clamped;
  }

  function handlePointerDown(e: React.PointerEvent, target: DragTarget) {
    (e.target as Element).setPointerCapture(e.pointerId);
    dragRef.current = target;
  }

  function handlePointerMove(e: React.PointerEvent) {
    const target = dragRef.current;
    const bar = barRef.current;
    if (!target || !bar) return;
    const rect = bar.getBoundingClientRect();
    const frac = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
    const t = windowStart + frac * (windowEnd - windowStart);
    if (target === "start") setStart(t);
    else setEnd(t);
  }

  function handlePointerUp() {
    dragRef.current = null;
  }

  // Clicking anywhere on the bar itself (not on a start/end handle) jumps
  // the preview video's playhead there - lets you scrub the source footage
  // directly instead of only being able to drag the two trim handles.
  function handleBarClick(e: React.MouseEvent) {
    const bar = barRef.current;
    if (!bar) return;
    const rect = bar.getBoundingClientRect();
    const frac = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
    const t = Math.max(0, Math.min(sourceDuration, windowStart + frac * (windowEnd - windowStart)));
    const v = videoRef.current;
    if (v) v.currentTime = t;
    setPlayheadTime(t);
  }

  function handlePreview() {
    const v = videoRef.current;
    if (!v) return;
    v.currentTime = trimStart;
    v.play().catch(() => {
      /* autoplay may be blocked - user can hit the native play control instead */
    });
    setPlayingPreview(true);
  }

  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    function onTimeUpdate() {
      if (v && playingPreview && v.currentTime >= trimEnd) {
        v.pause();
        setPlayingPreview(false);
      }
    }
    v.addEventListener("timeupdate", onTimeUpdate);
    return () => v.removeEventListener("timeupdate", onTimeUpdate);
  }, [playingPreview, trimEnd]);

  // Moving playhead on the timeline bar - tracks wherever the video actually
  // is (whether playing, scrubbed via the native controls, or previewing),
  // via rAF rather than the coarser `timeupdate` event so it moves smoothly.
  useEffect(() => {
    let raf: number;
    const tick = () => {
      const v = videoRef.current;
      if (v && !v.paused && !v.seeking) {
        setPlayheadTime(v.currentTime);
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);

  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    // Also catch single-frame seeks/pauses so the playhead still moves when
    // the user drags the native scrubber without ever entering "playing".
    const onSeeked = () => setPlayheadTime(v.currentTime);
    const onPause = () => setPlayheadTime(v.currentTime);
    v.addEventListener("seeked", onSeeked);
    v.addEventListener("pause", onPause);
    return () => {
      v.removeEventListener("seeked", onSeeked);
      v.removeEventListener("pause", onPause);
    };
  }, []);

  async function handleApply() {
    setApplying(true);
    setApplyError(null);
    try {
      const updated = await trimClip(jobId, index, trimStart, trimEnd);
      onUpdate(updated);
    } catch (e) {
      setApplyError(e instanceof Error ? e.message : "Could not adjust timing.");
    } finally {
      setApplying(false);
    }
  }

  const startFrac = (trimStart - windowStart) / (windowEnd - windowStart || 1);
  const endFrac = (trimEnd - windowStart) / (windowEnd - windowStart || 1);
  const changed = Math.abs(trimStart - clip.start) > 0.05 || Math.abs(trimEnd - clip.end) > 0.05;

  return (
    <div className="mt-2 rounded-xl border border-border bg-surface p-4">
      <div className="flex items-center justify-between">
        <p className="text-xs font-medium text-ink">
          Adjust timing for &ldquo;{clip.title}&rdquo;
        </p>
        <button onClick={onClose} className="text-xs text-ink-faint hover:text-ink-muted">
          Close
        </button>
      </div>
      <p className="mt-1 text-xs text-ink-muted">
        Drag either edge to extend or trim the clip - the whole source video is available, not
        just what was already cut. Type an exact time to jump further than the bar shows.
      </p>

      <video
        ref={videoRef}
        src={sourceFileUrl(jobId)}
        controls
        preload="auto"
        crossOrigin="anonymous"
        className="mt-2 w-full rounded-md bg-black"
        onLoadedMetadata={(e) => {
          e.currentTarget.currentTime = trimStart;
        }}
      />

      <div
        ref={barRef}
        className="relative mt-3 h-8 cursor-pointer select-none rounded bg-surface-elevated"
        style={{ touchAction: "none" }}
        onClick={handleBarClick}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerCancel={handlePointerUp}
      >
        {/* Selected range highlight */}
        <div
          className="absolute top-0 h-full bg-accent/30"
          style={{
            left: `${startFrac * 100}%`,
            width: `${Math.max(0, endFrac - startFrac) * 100}%`,
          }}
        />
        {/* Start handle */}
        <div
          onPointerDown={(e) => handlePointerDown(e, "start")}
          className="absolute top-0 h-full w-3 -translate-x-1/2 cursor-ew-resize rounded bg-emerald-500"
          style={{ left: `${startFrac * 100}%` }}
          title={formatClock(trimStart)}
        />
        {/* End handle */}
        <div
          onPointerDown={(e) => handlePointerDown(e, "end")}
          className="absolute top-0 h-full w-3 -translate-x-1/2 cursor-ew-resize rounded bg-sky-500"
          style={{ left: `${endFrac * 100}%` }}
          title={formatClock(trimEnd)}
        />
        {/* Playhead - where the video actually is right now, live */}
        {playheadTime !== null && playheadTime >= windowStart && playheadTime <= windowEnd && (
          <div
            className="pointer-events-none absolute top-0 h-full w-[2px] bg-red-500"
            style={{ left: `${((playheadTime - windowStart) / (windowEnd - windowStart || 1)) * 100}%` }}
          />
        )}
      </div>
      <div className="mt-1 flex justify-between text-[10px] text-ink-faint">
        <span>{formatClock(windowStart)}</span>
        <span>{formatClock(windowEnd)}</span>
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-3 text-xs">
        <label className="flex items-center gap-1.5">
          <span className="text-ink-muted">Start</span>
          <input
            value={startText}
            onChange={(e) => setStartText(e.target.value)}
            onBlur={() => {
              const t = parseClock(startText);
              if (t !== null) setStart(t);
              else setStartText(formatClock(trimStart));
            }}
            className="w-20 rounded border border-border bg-surface px-1.5 py-1 text-ink"
          />
        </label>
        <label className="flex items-center gap-1.5">
          <span className="text-ink-muted">End</span>
          <input
            value={endText}
            onChange={(e) => setEndText(e.target.value)}
            onBlur={() => {
              const t = parseClock(endText);
              if (t !== null) setEnd(t);
              else setEndText(formatClock(trimEnd));
            }}
            className="w-20 rounded border border-border bg-surface px-1.5 py-1 text-ink"
          />
        </label>
        <span className="text-ink-faint">({(trimEnd - trimStart).toFixed(1)}s)</span>

        <button
          onClick={handlePreview}
          className="rounded-md bg-surface-elevated px-2.5 py-1 text-xs font-medium text-ink hover:bg-surface-hover"
        >
          ▶ Preview
        </button>
        <button
          onClick={handleApply}
          disabled={applying || !changed}
          className="rounded-md bg-accent-solid px-3 py-1.5 text-xs font-medium text-[#faf6f0] hover:bg-accent-solid-hover disabled:opacity-50"
        >
          {applying ? "Re-cutting…" : "Apply new timing"}
        </button>
      </div>
      {applyError && <p className="mt-1 text-xs text-error">{applyError}</p>}
      {changed && !applying && (
        <p className="mt-1 text-xs text-warning">
          Applying re-cuts this clip and clears any 9:16 framing already done on it (the old crop
          was chosen against the old footage).
        </p>
      )}
    </div>
  );
}
