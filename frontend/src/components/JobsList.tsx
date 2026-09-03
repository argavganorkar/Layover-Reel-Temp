"use client";

import { useState } from "react";
import { Job, deleteJob, formatBytes, formatDuration, startTranscription } from "@/lib/api";
import ProgressBar from "./ProgressBar";
import ClipSelector from "./ClipSelector";

const STATUS_LABELS: Record<string, string> = {
  uploaded: "Uploaded",
  transcribing: "Transcribing",
  finding_moments: "Finding moments",
  cutting: "Cutting clips",
  reframing: "Reframing",
  captioning: "Generating captions",
  rendering: "Rendering",
  complete: "Complete",
  error: "Error",
};

function statusColor(status: string) {
  if (status === "complete") return "bg-success/15 text-success";
  if (status === "error") return "bg-error/15 text-error";
  if (status === "uploaded") return "bg-surface-elevated text-ink-muted";
  return "bg-accent/15 text-accent";
}

function ProbeInfo({ job }: { job: Job }) {
  if (job.probe_status === "pending" || job.probe_status === "running") {
    return (
      <div className="mt-2 max-w-xs">
        <ProgressBar label="Reading video with FFmpeg…" />
      </div>
    );
  }
  if (job.probe_status === "error") {
    return (
      <p className="mt-1 text-xs text-error">
        FFmpeg couldn&apos;t process this file: {job.probe_error ?? "unknown error"}
      </p>
    );
  }
  if (job.probe_status === "done" && job.media_info) {
    const mi = job.media_info;
    return (
      <p className="mt-1 text-xs text-ink-muted">
        {formatDuration(mi.duration_seconds)} · {mi.width}×{mi.height} · {mi.fps} fps ·{" "}
        {mi.has_audio ? "audio ✓" : "no audio track"}
        {job.audio_extracted ? " · audio extracted ✓" : ""}
      </p>
    );
  }
  return null;
}

function TranscribeControl({ job, onAction }: { job: Job; onAction: () => void }) {
  const [starting, setStarting] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);

  const canTranscribe = job.probe_status === "done" && job.audio_extracted;

  async function handleClick() {
    setStarting(true);
    setLocalError(null);
    try {
      await startTranscription(job.id);
    } catch (e) {
      setLocalError(e instanceof Error ? e.message : "Could not start transcription.");
    } finally {
      setStarting(false);
      // The backend has already moved this job to "pending"/"running" by the
      // time the request resolves - refresh now so the UI picks that up
      // immediately (and kicks the polling loop back on) instead of sitting
      // still until some other change happens to trigger it.
      onAction();
    }
  }

  if (!canTranscribe) return null;

  if (job.transcript_status === "pending" || job.transcript_status === "running") {
    return (
      <div className="mt-2 max-w-xs">
        <ProgressBar percent={job.transcript_progress} label="Transcribing…" />
      </div>
    );
  }

  if (job.transcript_status === "done" && job.transcript_summary) {
    const s = job.transcript_summary;
    return (
      <p className="mt-1 text-xs text-success">
        Transcript ready — {s.word_count} words, {s.segment_count} segments ({s.language})
      </p>
    );
  }

  if (job.transcript_status === "error") {
    return (
      <div className="mt-1">
        <p className="text-xs text-error">Transcription failed: {job.transcript_error}</p>
        <button
          onClick={handleClick}
          disabled={starting}
          className="mt-1 rounded-md bg-error/15 px-2 py-1 text-xs font-medium text-error hover:bg-error/25"
        >
          Retry
        </button>
      </div>
    );
  }

  return (
    <button
      onClick={handleClick}
      disabled={starting}
      className="mt-2 rounded-md bg-accent-solid px-3 py-1.5 text-xs font-medium text-[#faf6f0] hover:bg-accent-solid-hover disabled:opacity-50"
    >
      {starting ? "Starting…" : "Transcribe"}
      {localError && <span className="ml-2 text-[#ffd9cc]">{localError}</span>}
    </button>
  );
}

/**
 * Deleting a job removes its source video and everything derived from it
 * (clips, reels, captions) - irreversible, so this asks the user to confirm
 * inline before calling the API, rather than a browser `confirm()` popup.
 */
function DeleteJobControl({ job, onDeleted }: { job: Job; onDeleted: () => void }) {
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleConfirm() {
    setDeleting(true);
    setError(null);
    try {
      await deleteJob(job.id);
      onDeleted();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not delete.");
      setDeleting(false);
      setConfirming(false);
    }
  }

  if (confirming) {
    return (
      <div className="flex items-center gap-1.5 rounded-md border border-error/30 bg-error/10 px-2 py-1.5 text-xs">
        <span className="text-error">Delete this and everything made from it?</span>
        <button
          onClick={handleConfirm}
          disabled={deleting}
          className="ml-auto rounded-md bg-error px-2 py-1 font-medium text-[#241009] hover:opacity-90 disabled:opacity-50"
        >
          {deleting ? "Deleting…" : "Delete"}
        </button>
        <button
          onClick={() => setConfirming(false)}
          disabled={deleting}
          className="rounded-md bg-surface-elevated px-2 py-1 font-medium text-ink-muted hover:text-ink disabled:opacity-50"
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
        className="rounded-md px-1.5 py-1 text-xs font-medium text-ink-faint hover:bg-error/10 hover:text-error"
        title="Delete this upload and everything made from it"
      >
        Delete
      </button>
      {error && <p className="mt-1 text-xs text-error">{error}</p>}
    </div>
  );
}

export default function JobsList({
  jobs,
  onAction,
  title = "Your uploads",
  emptyMessage = "No videos uploaded yet. Upload one above to get started.",
}: {
  jobs: Job[];
  onAction: () => void;
  title?: string;
  emptyMessage?: string;
}) {
  if (jobs.length === 0) {
    return (
      <div className="flex h-full min-h-[160px] items-center justify-center rounded-2xl border border-dashed border-border bg-surface/40 p-6 text-center text-sm text-ink-muted">
        {emptyMessage}
      </div>
    );
  }

  return (
    <div className="shadow-warm w-full rounded-2xl border border-border bg-surface">
      <div className="border-b border-border px-5 py-4">
        <h2 className="font-serif text-lg font-medium text-ink">{title}</h2>
      </div>
      <ul className="divide-y divide-border">
        {jobs.map((job) => (
          <li key={job.id} className="p-5">
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <p className="truncate font-medium text-ink">{job.original_filename}</p>
                <p className="text-xs text-ink-faint">
                  {formatBytes(job.size_bytes)} · {new Date(job.created_at).toLocaleString()}
                </p>
                <ProbeInfo job={job} />
                <TranscribeControl job={job} onAction={onAction} />
              </div>
              <div className="flex shrink-0 flex-col items-end gap-2">
                <span
                  className={`rounded-md px-3 py-1 text-xs font-medium ${statusColor(job.status)}`}
                >
                  {STATUS_LABELS[job.status] ?? job.status}
                </span>
                <DeleteJobControl job={job} onDeleted={onAction} />
              </div>
            </div>
            <ClipSelector job={job} onAction={onAction} />
          </li>
        ))}
      </ul>
    </div>
  );
}
