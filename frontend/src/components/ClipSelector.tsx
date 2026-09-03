"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  Clip,
  ClipRequest,
  ContentPreference,
  Job,
  captionedReelDownloadUrl,
  clearOutro,
  clearStyle,
  clipDownloadUrl,
  fetchClips,
  formatDuration,
  outroReelDownloadUrl,
  reelDownloadUrl,
  startClipSelection,
  startCutting,
  startOutroRender,
  startStyleRender,
  styledReelDownloadUrl,
} from "@/lib/api";
import ProgressBar from "./ProgressBar";

const CONTENT_PREFERENCES: { value: ContentPreference; label: string }[] = [
  { value: "best", label: "Best moments" },
  { value: "educational", label: "Educational" },
  { value: "funny", label: "Funny" },
  { value: "storytelling", label: "Storytelling" },
  { value: "controversial", label: "Controversial" },
  { value: "emotional", label: "Emotional" },
];

export function formatTimeRange(start: number, end: number): string {
  const fmt = (s: number) => {
    const m = Math.floor(s / 60);
    const sec = Math.round(s % 60);
    return `${m}:${sec.toString().padStart(2, "0")}`;
  };
  return `${fmt(start)} - ${fmt(end)} (${Math.round(end - start)}s)`;
}

// --- Consolidated "just give me the file" download control -----------------
// Every finished stage produces its own downloadable file; rather than
// scattering a separate download link next to each stage (the "so many
// options" clutter), this collects all of them, best-first, behind one
// button - with the rest a click away instead of always on screen.

function downloadOptions(jobId: string, index: number, clip: Clip): { label: string; url: string }[] {
  const opts: { label: string; url: string }[] = [];
  if (clip.outro_status === "done") opts.push({ label: "With outro", url: outroReelDownloadUrl(jobId, index) });
  if (clip.caption_status === "done") opts.push({ label: "Captioned", url: captionedReelDownloadUrl(jobId, index) });
  if (clip.style_status === "done") opts.push({ label: "Styled (no captions)", url: styledReelDownloadUrl(jobId, index) });
  if (clip.reframe_status === "done") opts.push({ label: "9:16 reel", url: reelDownloadUrl(jobId, index) });
  if (clip.cut_status === "done") opts.push({ label: "Original clip", url: clipDownloadUrl(jobId, index) });
  return opts;
}

export function DownloadMenu({ jobId, index, clip }: { jobId: string; index: number; clip: Clip }) {
  const [showAll, setShowAll] = useState(false);
  const options = downloadOptions(jobId, index, clip);
  if (options.length === 0) return null;
  const [best, ...rest] = options;

  return (
    <div className="mt-3 flex flex-wrap items-center gap-2">
      <a
        href={best.url}
        download
        target="_blank"
        rel="noopener noreferrer"
        className="rounded-md bg-accent-solid px-3 py-1.5 text-xs font-medium text-[#faf6f0] hover:bg-accent-solid-hover"
      >
        Download ({best.label}) ↓
      </a>
      {rest.length > 0 && (
        <button
          onClick={() => setShowAll((v) => !v)}
          className="text-xs font-medium text-ink-faint hover:text-ink-muted"
        >
          {showAll ? "hide other versions" : `${rest.length} other version${rest.length > 1 ? "s" : ""} ▾`}
        </button>
      )}
      {showAll &&
        rest.map((o) => (
          <a
            key={o.label}
            href={o.url}
            download
            target="_blank"
            rel="noopener noreferrer"
            className="rounded-md bg-surface-elevated px-2.5 py-1 text-xs font-medium text-ink-muted hover:bg-surface-hover"
          >
            {o.label} ↓
          </a>
        ))}
    </div>
  );
}

// --- Stage stepper -----------------------------------------------------
// One compact row standing in for what used to be five-to-nine separate
// buttons shown all at once. Each pill is a stage's current status; click
// one to expand just that stage's controls below (an accordion - only one
// open at a time), instead of every stage's full control set sitting on
// screen regardless of whether it's relevant right now.

export type StageStatus = "locked" | "available" | "pending" | "running" | "done" | "error";

/**
 * Stage stepper - a compact row of pills, one per editing stage. Each pill
 * navigates into that stage's own tab on the dedicated per-clip workspace
 * page instead of expanding an inline accordion panel here, so "working in
 * one particular frame" gets a whole roomy screen rather than a sliver of
 * a card.
 */
function StageLink({
  href,
  label,
  status,
}: {
  href: string;
  label: string;
  status: StageStatus;
}) {
  const locked = status === "locked";
  let cls =
    "flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs font-medium transition-colors ";
  if (status === "done") cls += "border-success/30 bg-success/10 text-success hover:bg-success/15";
  else if (status === "running" || status === "pending")
    cls += "border-accent/30 bg-accent/10 text-accent hover:bg-accent/15";
  else if (status === "error") cls += "border-error/30 bg-error/10 text-error hover:bg-error/15";
  else if (locked) cls += "border-border bg-surface text-ink-faint/60 cursor-not-allowed";
  else cls += "border-border bg-surface text-ink-muted hover:bg-surface-hover";

  const content = (
    <>
      {status === "done" && <span>✓</span>}
      {(status === "running" || status === "pending") && (
        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" />
      )}
      {status === "error" && <span>!</span>}
      {label}
    </>
  );

  if (locked) {
    return (
      <span className={cls} aria-disabled="true">
        {content}
      </span>
    );
  }

  return (
    <Link href={href} className={cls}>
      {content}
    </Link>
  );
}

export function ClipCutStatus({ clip }: { clip: Clip }) {
  if (clip.cut_status === "pending" || clip.cut_status === "running") {
    return (
      <div className="mt-2 max-w-xs">
        <ProgressBar label="Cutting with FFmpeg…" />
      </div>
    );
  }
  if (clip.cut_status === "error") {
    return <p className="mt-2 text-xs text-error">Cutting failed: {clip.cut_error}</p>;
  }
  return null;
}

/** "~7 min remaining", "~40s remaining", or null if there isn't enough
 * progress data yet to estimate (right after starting). */
function estimateStyleRemaining(clip: Clip): string | null {
  const { style_started_at, style_frames_done, style_frames_total } = clip;
  if (!style_started_at || !style_frames_done || !style_frames_total || style_frames_done <= 0) {
    return null;
  }
  const elapsedSeconds = (Date.now() - new Date(style_started_at).getTime()) / 1000;
  if (elapsedSeconds <= 0) return null;

  const framesPerSecond = style_frames_done / elapsedSeconds;
  const framesLeft = Math.max(0, style_frames_total - style_frames_done);
  const remainingSeconds = framesPerSecond > 0 ? framesLeft / framesPerSecond : 0;

  if (remainingSeconds < 45) return "~1 min remaining";
  const minutes = Math.round(remainingSeconds / 60);
  return `~${minutes} min remaining`;
}

export function stageStatus(status: string | null | undefined, locked: boolean): StageStatus {
  if (locked) return "locked";
  if (status === "pending" || status === "running") return "running";
  if (status === "done") return "done";
  if (status === "error") return "error";
  return "available";
}

export function StylePanel({
  jobId,
  index,
  clip,
  onUpdate,
}: {
  jobId: string;
  index: number;
  clip: Clip;
  onUpdate: (clip: Clip) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);

  async function handleApply() {
    setConfirming(false);
    setBusy(true);
    setLocalError(null);
    try {
      onUpdate(await startStyleRender(jobId, index));
    } catch (e) {
      setLocalError(e instanceof Error ? e.message : "Could not apply the style.");
    } finally {
      setBusy(false);
    }
  }

  async function handleRemove() {
    setBusy(true);
    setLocalError(null);
    try {
      onUpdate(await clearStyle(jobId, index));
    } catch (e) {
      setLocalError(e instanceof Error ? e.message : "Could not remove the style.");
    } finally {
      setBusy(false);
    }
  }

  if (clip.style_status === "pending" || clip.style_status === "running") {
    const remaining = estimateStyleRemaining(clip);
    const percent =
      clip.style_frames_done && clip.style_frames_total
        ? (clip.style_frames_done / clip.style_frames_total) * 100
        : undefined;
    return (
      <div className="max-w-sm">
        <ProgressBar percent={percent} label={`Applying reference style… ${remaining ?? "estimating time remaining…"}`} />
        <p className="mt-1 text-[11px] text-ink-faint">Safe to leave this running and check back later.</p>
      </div>
    );
  }

  if (clip.style_status === "done") {
    return (
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-ink-muted">Reference style applied.</span>
        <button
          onClick={handleRemove}
          disabled={busy}
          className="rounded-md px-2 py-1 text-xs font-medium text-ink-faint hover:bg-error/10 hover:text-error disabled:opacity-50"
        >
          Remove
        </button>
        {localError && <p className="w-full text-xs text-error">{localError}</p>}
      </div>
    );
  }

  if (confirming) {
    return (
      <div className="flex flex-wrap items-center gap-1.5 rounded-md border border-accent/30 bg-accent/10 px-2 py-1.5 text-xs">
        <span className="text-accent">
          This takes roughly 8-10 minutes for a typical clip (longer for a longer one) - there&apos;s
          no way to stop it once it starts. Begin anyway?
        </span>
        <button
          onClick={handleApply}
          className="ml-auto rounded-md bg-accent-solid px-2 py-1 font-medium text-[#faf6f0] hover:bg-accent-solid-hover"
        >
          Start
        </button>
        <button
          onClick={() => setConfirming(false)}
          className="rounded-md bg-surface px-2 py-1 font-medium text-ink-muted hover:bg-surface-elevated"
        >
          Cancel
        </button>
      </div>
    );
  }

  return (
    <div>
      <button
        onClick={() => setConfirming(true)}
        disabled={busy}
        title="Clean black-and-white subject cutout on white, with a subtle paper texture - matching the reference video"
        className="rounded-md bg-surface-elevated px-2.5 py-1 text-xs font-medium text-ink hover:bg-surface-hover disabled:opacity-50"
      >
        {busy ? "Starting…" : "Apply reference style ✨"}
      </button>
      {clip.style_status === "error" && (
        <p className="mt-1 text-xs text-error">Style failed: {clip.style_error}</p>
      )}
      {localError && <p className="mt-1 text-xs text-error">{localError}</p>}
    </div>
  );
}

/**
 * Adds the bundled outro clip onto the end of whatever's the most-finished
 * version of this reel right now (captioned > styled > plain 9:16 - see
 * storage.most_finished_reel_path).
 */
export function OutroPanel({
  jobId,
  index,
  clip,
  onUpdate,
}: {
  jobId: string;
  index: number;
  clip: Clip;
  onUpdate: (clip: Clip) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);

  async function handleAdd() {
    setBusy(true);
    setLocalError(null);
    try {
      onUpdate(await startOutroRender(jobId, index));
    } catch (e) {
      setLocalError(e instanceof Error ? e.message : "Could not add the outro.");
    } finally {
      setBusy(false);
    }
  }

  async function handleRemove() {
    setBusy(true);
    setLocalError(null);
    try {
      onUpdate(await clearOutro(jobId, index));
    } catch (e) {
      setLocalError(e instanceof Error ? e.message : "Could not remove the outro.");
    } finally {
      setBusy(false);
    }
  }

  if (clip.outro_status === "pending" || clip.outro_status === "running") {
    return (
      <div className="max-w-sm">
        <ProgressBar label="Adding outro…" />
      </div>
    );
  }

  if (clip.outro_status === "done") {
    return (
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-ink-muted">Outro added, onto the most finished version of this reel.</span>
        <button
          onClick={handleRemove}
          disabled={busy}
          className="rounded-md px-2 py-1 text-xs font-medium text-ink-faint hover:bg-error/10 hover:text-error disabled:opacity-50"
        >
          Remove
        </button>
        {localError && <p className="w-full text-xs text-error">{localError}</p>}
      </div>
    );
  }

  return (
    <div>
      <button
        onClick={handleAdd}
        disabled={busy}
        title="Appends the outro to whichever version of this reel is most finished right now (captioned, styled, or plain)"
        className="rounded-md bg-surface-elevated px-2.5 py-1 text-xs font-medium text-ink hover:bg-surface-hover disabled:opacity-50"
      >
        {busy ? "Starting…" : "Add outro 🎬"}
      </button>
      {clip.outro_status === "error" && (
        <p className="mt-1 text-xs text-error">Outro failed: {clip.outro_error}</p>
      )}
      {localError && <p className="mt-1 text-xs text-error">{localError}</p>}
    </div>
  );
}

function ClipCard({ jobId, index, clip }: { jobId: string; index: number; clip: Clip }) {
  const framed = clip.reframe_status === "done";
  const canFrame = clip.cut_status === "done";
  const base = `/clip/${jobId}/${index}`;

  const frameStatus = stageStatus(clip.reframe_status, !canFrame);
  const styleStatusChip = stageStatus(clip.style_status, !framed);
  const captionStatusChip = stageStatus(clip.caption_status, !framed);
  const outroStatusChip = stageStatus(clip.outro_status, !framed);

  return (
    <div className="rounded-xl border border-border bg-surface-hover/40 p-4">
      <div className="flex items-start justify-between gap-2">
        {clip.cut_status === "done" ? (
          <Link href={`${base}?tab=frame`} className="font-medium text-ink hover:text-accent">
            {clip.title}
          </Link>
        ) : (
          <p className="font-medium text-ink">{clip.title}</p>
        )}
        <span className="shrink-0 rounded-md bg-accent/15 px-2 py-0.5 text-xs font-semibold text-accent">
          {clip.score}
        </span>
      </div>
      <p className="mt-0.5 text-xs text-ink-muted">{formatTimeRange(clip.start, clip.end)}</p>
      <p className="mt-2 text-sm italic text-ink-muted">&ldquo;{clip.hook}&rdquo;</p>
      <p className="mt-1 text-xs text-ink-faint">{clip.reason}</p>
      <ClipCutStatus clip={clip} />

      {clip.cut_status === "done" && (
        <>
          <div className="mt-3 flex flex-wrap gap-1.5">
            <StageLink href={`${base}?tab=trim`} label="⏱ Timing" status="available" />
            <StageLink href={`${base}?tab=frame`} label="Frame 9:16" status={frameStatus} />
            <StageLink href={`${base}?tab=style`} label="Style" status={styleStatusChip} />
            <StageLink href={`${base}?tab=captions`} label="Captions" status={captionStatusChip} />
            <StageLink href={`${base}?tab=outro`} label="Outro" status={outroStatusChip} />
          </div>
          <DownloadMenu jobId={jobId} index={index} clip={clip} />
        </>
      )}
    </div>
  );
}

export default function ClipSelector({ job, onAction }: { job: Job; onAction: () => void }) {
  const [numClips, setNumClips] = useState<ClipRequest["num_clips"]>(5);
  const [length, setLength] = useState<ClipRequest["target_length_seconds"]>(45);
  const [preference, setPreference] = useState<ContentPreference>("best");
  const [starting, setStarting] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);
  const [clips, setClips] = useState<Clip[] | null>(null);
  const [cutting, setCutting] = useState(false);
  const [cutError, setCutError] = useState<string | null>(null);

  const canStart = job.transcript_status === "done";

  useEffect(() => {
    if (job.clips_status === "done") {
      fetchClips(job.id)
        .then(setClips)
        .catch(() => {
          /* transient, will retry on next poll-driven re-render */
        });
    }
    // Re-fetch whenever cut_status changes too (pending -> running -> done),
    // since cut progress is stored per-clip inside clips.json, not on the
    // job object itself - the job-level poll alone wouldn't pick it up.
  }, [job.clips_status, job.cut_status, job.id]);

  // While any clip is being reframed, or individually re-cut after a timing
  // adjustment (Phase 6.5), poll clips.json until it settles - both kinds of
  // progress live per-clip, same reasoning as job.cut_status above, but
  // scoped here since they're per-clip actions, not job-wide ones tracked
  // on the job object. A per-clip re-cut in particular doesn't touch
  // job.cut_status at all, so the job-level poll above wouldn't catch it.
  useEffect(() => {
    const anyBusy = clips?.some(
      (c) =>
        c.reframe_status === "pending" ||
        c.reframe_status === "running" ||
        c.cut_status === "pending" ||
        c.cut_status === "running" ||
        c.caption_status === "pending" ||
        c.caption_status === "running" ||
        c.style_status === "pending" ||
        c.style_status === "running" ||
        c.outro_status === "pending" ||
        c.outro_status === "running"
    );
    if (!anyBusy) return;
    const interval = setInterval(() => {
      fetchClips(job.id)
        .then(setClips)
        .catch(() => {
          /* transient, will retry next tick */
        });
    }, 1500);
    return () => clearInterval(interval);
  }, [clips, job.id]);

  if (!canStart) return null;

  async function handleStart() {
    setStarting(true);
    setLocalError(null);
    try {
      await startClipSelection(job.id, {
        num_clips: numClips,
        target_length_seconds: length,
        content_preference: preference,
      });
    } catch (e) {
      setLocalError(e instanceof Error ? e.message : "Could not start clip selection.");
    } finally {
      setStarting(false);
      onAction();
    }
  }

  async function handleCut() {
    setCutting(true);
    setCutError(null);
    try {
      await startCutting(job.id);
    } catch (e) {
      setCutError(e instanceof Error ? e.message : "Could not start cutting clips.");
    } finally {
      setCutting(false);
      onAction();
    }
  }

  const isRunning = job.clips_status === "pending" || job.clips_status === "running";
  const showForm = !job.clips_status || job.clips_status === "error";

  return (
    <div className="mt-4 border-t border-border pt-4">
      <p className="text-sm font-medium text-ink">2. Find the best moments</p>

      {showForm && (
        <div className="mt-2 space-y-2">
          <div className="flex flex-wrap gap-3 text-xs">
            <label className="flex items-center gap-1.5">
              <span className="text-ink-muted">Clips:</span>
              <select
                value={numClips}
                onChange={(e) => setNumClips(Number(e.target.value) as ClipRequest["num_clips"])}
                className="rounded border border-border bg-surface px-1.5 py-1 text-ink"
              >
                <option value={3}>3</option>
                <option value={5}>5</option>
                <option value={10}>10</option>
              </select>
            </label>
            <label className="flex items-center gap-1.5">
              <span className="text-ink-muted">Length:</span>
              <select
                value={length}
                onChange={(e) =>
                  setLength(Number(e.target.value) as ClipRequest["target_length_seconds"])
                }
                className="rounded border border-border bg-surface px-1.5 py-1 text-ink"
              >
                <option value={30}>~30s</option>
                <option value={45}>~45s</option>
                <option value={60}>~60s</option>
              </select>
            </label>
            <label className="flex items-center gap-1.5">
              <span className="text-ink-muted">Style:</span>
              <select
                value={preference}
                onChange={(e) => setPreference(e.target.value as ContentPreference)}
                className="rounded border border-border bg-surface px-1.5 py-1 text-ink"
              >
                {CONTENT_PREFERENCES.map((p) => (
                  <option key={p.value} value={p.value}>
                    {p.label}
                  </option>
                ))}
              </select>
            </label>
          </div>

          {job.clips_status === "error" && (
            <p className="text-xs text-error">{job.clips_error}</p>
          )}
          {localError && <p className="text-xs text-error">{localError}</p>}

          <button
            onClick={handleStart}
            disabled={starting}
            className="rounded-md bg-accent-solid px-3 py-1.5 text-xs font-medium text-[#faf6f0] hover:bg-accent-solid-hover disabled:opacity-50"
          >
            {starting ? "Starting…" : job.clips_status === "error" ? "Retry" : "Find best moments"}
          </button>
        </div>
      )}

      {isRunning && (
        <div className="mt-2 max-w-sm">
          <ProgressBar label="Reading the transcript and picking the best moments…" />
        </div>
      )}

      {job.clips_status === "done" && clips && (
        <div className="mt-3 space-y-3">
          <p className="text-xs text-success">
            Found {clips.length} clip{clips.length === 1 ? "" : "s"} · total video was{" "}
            {formatDuration(job.media_info?.duration_seconds ?? 0)}
          </p>

          {!job.cut_status && (
            <button
              onClick={handleCut}
              disabled={cutting}
              className="rounded-md bg-accent-solid px-3 py-1.5 text-xs font-medium text-[#faf6f0] hover:bg-accent-solid-hover disabled:opacity-50"
            >
              {cutting ? "Starting…" : "Cut clips"}
            </button>
          )}
          {(job.cut_status === "pending" || job.cut_status === "running") && (
            <div className="max-w-sm">
              <ProgressBar label="Cutting clips with FFmpeg…" />
            </div>
          )}
          {job.cut_status === "error" && (
            <div>
              <p className="text-xs text-error">{job.cut_error}</p>
              <button
                onClick={handleCut}
                disabled={cutting}
                className="mt-1 rounded-md bg-error/10 px-2 py-1 text-xs font-medium text-error hover:bg-error/20"
              >
                Retry
              </button>
            </div>
          )}
          {cutError && <p className="text-xs text-error">{cutError}</p>}

          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
            {clips.map((clip, i) => (
              <ClipCard key={i} jobId={job.id} index={i} clip={clip} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
