"use client";

import { useEffect, useState } from "react";
import {
  Clip,
  Job,
  captionedReelDownloadUrl,
  clipDownloadUrl,
  fetchClips,
  formatDuration,
  outroReelDownloadUrl,
  reelDownloadUrl,
  styledReelDownloadUrl,
} from "@/lib/api";

/**
 * Phase 9: one place to see and download everything that's ready, instead
 * of scrolling through each job's full pipeline to find a finished clip.
 * Purely a frontend view over data the backend already exposes (fetchClips
 * per job) - no new endpoints needed. For each clip, shows the single most
 * finished version available: with outro (which already has any applied
 * style/captions baked in) > captioned reel > styled reel > plain 9:16 reel
 * > raw cut clip, so a clip never appears twice.
 */

type Stage = "outro" | "captioned" | "styled" | "reel" | "clip";

interface ResultRow {
  key: string;
  job: Job;
  clip: Clip;
  stage: Stage;
  videoUrl: string;
  downloadUrl: string;
}

const STAGE_LABEL: Record<Stage, string> = {
  outro: "With outro",
  captioned: "Captioned reel",
  styled: "Styled (no captions yet)",
  reel: "9:16 reel (no captions yet)",
  clip: "Clip (not framed yet)",
};

const STAGE_COLOR: Record<Stage, string> = {
  outro: "bg-success/15 text-success",
  captioned: "bg-success/15 text-success",
  styled: "bg-accent/15 text-accent",
  reel: "bg-accent/15 text-accent",
  clip: "bg-surface-elevated text-ink-muted",
};

function bestResultFor(job: Job, clip: Clip, index: number): ResultRow | null {
  if (clip.outro_status === "done") {
    const url = outroReelDownloadUrl(job.id, index);
    return { key: `${job.id}-${index}`, job, clip, stage: "outro", videoUrl: url, downloadUrl: url };
  }
  if (clip.caption_status === "done") {
    const url = captionedReelDownloadUrl(job.id, index);
    return { key: `${job.id}-${index}`, job, clip, stage: "captioned", videoUrl: url, downloadUrl: url };
  }
  if (clip.style_status === "done") {
    const url = styledReelDownloadUrl(job.id, index);
    return { key: `${job.id}-${index}`, job, clip, stage: "styled", videoUrl: url, downloadUrl: url };
  }
  if (clip.reframe_status === "done") {
    const url = reelDownloadUrl(job.id, index);
    return { key: `${job.id}-${index}`, job, clip, stage: "reel", videoUrl: url, downloadUrl: url };
  }
  if (clip.cut_status === "done") {
    const url = clipDownloadUrl(job.id, index);
    return { key: `${job.id}-${index}`, job, clip, stage: "clip", videoUrl: url, downloadUrl: url };
  }
  return null;
}

async function loadAllClips(jobs: Job[]): Promise<Record<string, Clip[]>> {
  const entries = await Promise.all(
    jobs.map(async (j) => {
      try {
        return [j.id, await fetchClips(j.id)] as const;
      } catch {
        // transient - next poll/refresh tick will retry
        return [j.id, [] as Clip[]] as const;
      }
    })
  );
  return Object.fromEntries(entries);
}

export default function ResultsGallery({ jobs }: { jobs: Job[] }) {
  const [clipsByJob, setClipsByJob] = useState<Record<string, Clip[]>>({});
  const jobsWithClips = jobs.filter((j) => j.clips_status === "done");
  const jobIdsKey = jobsWithClips.map((j) => j.id).join(",");

  // Reload whenever the set of jobs that have clips changes (a new job
  // finished clip selection, or the page's own polling refreshed `jobs`).
  useEffect(() => {
    let cancelled = false;
    loadAllClips(jobsWithClips).then((data) => {
      if (!cancelled) setClipsByJob(data);
    });
    return () => {
      cancelled = true;
    };
    // jobIdsKey captures the only thing that should retrigger this - which
    // jobs are in scope, not every field on them (avoids an effect loop
    // from page.tsx's own polling handing down a new `jobs` array).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobIdsKey]);

  // While any clip we know about is still mid-pipeline (reframing,
  // captioning, or a re-cut), poll independently of page.tsx's own polling -
  // that loop stops once job.clips_status/cut_status settle, well before a
  // per-clip reframe or caption render actually finishes.
  useEffect(() => {
    const anyBusy = Object.values(clipsByJob)
      .flat()
      .some(
        (c) =>
          c.cut_status === "pending" ||
          c.cut_status === "running" ||
          c.reframe_status === "pending" ||
          c.reframe_status === "running" ||
          c.caption_status === "pending" ||
          c.caption_status === "running" ||
          c.style_status === "pending" ||
          c.style_status === "running" ||
          c.outro_status === "pending" ||
          c.outro_status === "running"
      );
    if (!anyBusy || jobsWithClips.length === 0) return;
    const interval = setInterval(() => {
      loadAllClips(jobsWithClips).then(setClipsByJob);
    }, 3000);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clipsByJob, jobIdsKey]);

  const results: ResultRow[] = [];
  for (const job of jobsWithClips) {
    const clips = clipsByJob[job.id] ?? [];
    clips.forEach((clip, index) => {
      const r = bestResultFor(job, clip, index);
      if (r) results.push(r);
    });
  }

  if (results.length === 0) return null;

  return (
    <div className="shadow-warm w-full rounded-2xl border border-border bg-surface">
      <div className="border-b border-border px-5 py-4">
        <h2 className="font-serif text-lg font-medium text-ink">Your reels</h2>
        <p className="mt-0.5 text-xs text-ink-muted">
          Everything that&apos;s ready, in one place - each clip shows its most finished version.
        </p>
      </div>
      <div className="grid grid-cols-2 gap-4 p-5 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
        {results.map((r) => (
          <div key={r.key} className="overflow-hidden rounded-lg border border-border bg-surface-elevated">
            <video
              src={`${r.videoUrl}#t=0.1`}
              controls
              preload="metadata"
              className="aspect-[9/16] w-full bg-black object-contain"
            />
            <div className="p-2">
              <p className="truncate text-xs font-medium text-ink" title={r.clip.title}>
                {r.clip.title}
              </p>
              <p className="mt-0.5 truncate text-[10px] text-ink-faint" title={r.job.original_filename}>
                {r.job.original_filename} · {formatDuration(r.clip.end - r.clip.start)}
              </p>
              <span
                className={`mt-1 inline-block rounded-md px-2 py-0.5 text-[10px] font-medium ${STAGE_COLOR[r.stage]}`}
              >
                {STAGE_LABEL[r.stage]}
              </span>
              <a
                href={r.downloadUrl}
                download
                target="_blank"
                rel="noopener noreferrer"
                className="mt-2 block rounded-md bg-accent-solid px-2 py-1 text-center text-[11px] font-medium text-[#faf6f0] hover:bg-accent-solid-hover"
              >
                Download ↓
              </a>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
