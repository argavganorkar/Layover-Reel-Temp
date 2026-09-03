"use client";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import {
  Clip,
  Job,
  fetchClips,
  fetchJob,
} from "@/lib/api";
import {
  ClipCutStatus,
  DownloadMenu,
  OutroPanel,
  StageStatus,
  StylePanel,
  formatTimeRange,
  stageStatus,
} from "@/components/ClipSelector";
import TrimEditor from "@/components/TrimEditor";
import FrameEditor from "@/components/FrameEditor";
import CaptionEditor from "@/components/CaptionEditor";

type TabKey = "trim" | "frame" | "style" | "captions" | "outro";

const TABS: { key: TabKey; label: string }[] = [
  { key: "trim", label: "Timing" },
  { key: "frame", label: "Frame 9:16" },
  { key: "style", label: "Style" },
  { key: "captions", label: "Captions" },
  { key: "outro", label: "Outro" },
];

function isBusy(clip: Clip): boolean {
  return [
    clip.cut_status,
    clip.reframe_status,
    clip.caption_status,
    clip.style_status,
    clip.outro_status,
  ].some((s) => s === "pending" || s === "running");
}

/** Sidebar item on desktop, tab pill on mobile - same data, two layouts. */
function TabNavItem({
  tab,
  status,
  active,
  href,
  vertical,
}: {
  tab: { key: TabKey; label: string };
  status: StageStatus;
  active: boolean;
  href: string;
  vertical: boolean;
}) {
  const locked = status === "locked";
  const base = vertical
    ? "flex items-center gap-2.5 rounded-lg px-3.5 py-2.5 text-sm font-medium transition-colors"
    : "flex shrink-0 items-center gap-1.5 rounded-full px-3.5 py-2 text-xs font-medium transition-colors";

  let cls = `${base} `;
  if (active) cls += "bg-accent-solid text-[#faf6f0]";
  else if (locked) cls += "text-ink-faint/60 cursor-not-allowed";
  else if (status === "done") cls += "text-success hover:bg-success/10";
  else if (status === "running" || status === "pending") cls += "text-accent hover:bg-accent/10";
  else if (status === "error") cls += "text-error hover:bg-error/10";
  else cls += "text-ink-muted hover:bg-surface-hover hover:text-ink";

  const indicator =
    status === "done" ? (
      <span className={active ? "text-[#faf6f0]" : "text-success"}>✓</span>
    ) : status === "running" || status === "pending" ? (
      <span className={`h-1.5 w-1.5 animate-pulse rounded-full ${active ? "bg-[#faf6f0]" : "bg-current"}`} />
    ) : status === "error" ? (
      <span className={active ? "text-[#faf6f0]" : "text-error"}>!</span>
    ) : null;

  if (locked) {
    return (
      <span className={cls} aria-disabled="true" title="Frame this clip to 9:16 first">
        {tab.label}
        {indicator}
      </span>
    );
  }

  return (
    <Link href={href} className={cls}>
      {tab.label}
      {indicator}
    </Link>
  );
}

function LockedStagePanel({ base }: { base: string }) {
  return (
    <div className="rounded-xl border border-border bg-surface p-8 text-center">
      <p className="text-sm text-ink-muted">Frame this clip to 9:16 first - style, captions, and outro all build on top of the framed reel.</p>
      <Link
        href={`${base}?tab=frame`}
        className="mt-3 inline-block rounded-md bg-accent-solid px-3.5 py-1.5 text-xs font-medium text-[#faf6f0] hover:bg-accent-solid-hover"
      >
        Go to Frame 9:16
      </Link>
    </div>
  );
}

function ClipWorkspaceInner() {
  const params = useParams<{ jobId: string; index: string }>();
  const searchParams = useSearchParams();
  const router = useRouter();
  const jobId = params.jobId;
  const index = Number(params.index);
  const tab = (searchParams.get("tab") as TabKey | null) ?? "frame";
  const base = `/clip/${jobId}/${index}`;

  const [job, setJob] = useState<Job | null>(null);
  const [clips, setClips] = useState<Clip[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([fetchJob(jobId), fetchClips(jobId)])
      .then(([j, c]) => {
        if (cancelled) return;
        setJob(j);
        setClips(c);
      })
      .catch((e) => {
        if (cancelled) return;
        setLoadError(e instanceof Error ? e.message : "Could not load this clip.");
      });
    return () => {
      cancelled = true;
    };
  }, [jobId]);

  // Poll while anything on this clip is still processing, so status/preview
  // catch up without the user needing to refresh.
  useEffect(() => {
    const clip = clips?.[index];
    if (!clip || !isBusy(clip)) return;
    const interval = setInterval(() => {
      fetchClips(jobId)
        .then(setClips)
        .catch(() => {
          /* transient, will retry next tick */
        });
    }, 1500);
    return () => clearInterval(interval);
  }, [clips, index, jobId]);

  function handleClipUpdate(updated: Clip) {
    setClips((prev) => (prev ? prev.map((c, i) => (i === index ? updated : c)) : prev));
  }

  if (loadError) {
    return (
      <main className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-4 px-6 py-10 sm:px-10">
        <p className="text-sm text-error">{loadError}</p>
        <Link href="/history" className="text-sm text-accent hover:underline">
          ← Back to History
        </Link>
      </main>
    );
  }

  if (!job || !clips) {
    return (
      <main className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-4 px-6 py-10 sm:px-10">
        <p className="text-sm text-ink-muted">Loading…</p>
      </main>
    );
  }

  const clip = clips[index];
  if (!clip) {
    return (
      <main className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-4 px-6 py-10 sm:px-10">
        <p className="text-sm text-error">That clip couldn&rsquo;t be found.</p>
        <Link href="/history" className="text-sm text-accent hover:underline">
          ← Back to History
        </Link>
      </main>
    );
  }

  if (clip.cut_status !== "done") {
    return (
      <main className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-4 px-6 py-10 sm:px-10">
        <Link href="/history" className="text-sm text-accent hover:underline">
          ← Back to History
        </Link>
        <div className="rounded-xl border border-border bg-surface p-8 text-center">
          <p className="text-sm text-ink-muted">
            This clip hasn&rsquo;t finished cutting yet{clip.cut_status === "error" ? " (it failed)" : ""}.
          </p>
          <ClipCutStatus clip={clip} />
        </div>
      </main>
    );
  }

  const framed = clip.reframe_status === "done";
  const canFrame = clip.cut_status === "done";
  const sourceDuration = job.media_info?.duration_seconds;

  const statuses: Record<TabKey, StageStatus> = {
    trim: "available",
    frame: stageStatus(clip.reframe_status, !canFrame),
    style: stageStatus(clip.style_status, !framed),
    captions: stageStatus(clip.caption_status, !framed),
    outro: stageStatus(clip.outro_status, !framed),
  };

  const activeTab = statuses[tab] === "locked" ? "frame" : tab;

  return (
    <main className="mx-auto flex w-full max-w-[1400px] flex-1 flex-col gap-5 px-4 py-6 sm:px-6 lg:px-8">
      {/* --- Header: identity, status, and the one place downloads live --- */}
      <div>
        <Link href="/history" className="text-xs font-medium text-ink-faint hover:text-ink-muted">
          ← Back to History
        </Link>
        <div className="mt-2 flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2.5">
              <h1 className="truncate font-serif text-2xl font-medium tracking-tight text-ink">{clip.title}</h1>
              <span className="shrink-0 rounded-md bg-accent/15 px-2 py-0.5 text-xs font-semibold text-accent">
                {clip.score}
              </span>
            </div>
            <p className="mt-0.5 text-xs text-ink-muted">{formatTimeRange(clip.start, clip.end)}</p>
            <p className="mt-1.5 max-w-2xl text-sm italic text-ink-muted">&ldquo;{clip.hook}&rdquo;</p>
            <p className="mt-1 max-w-2xl text-xs text-ink-faint">{clip.reason}</p>
          </div>
          <DownloadMenu jobId={jobId} index={index} clip={clip} />
        </div>
      </div>

      {/* --- Tabs: sidebar on desktop, horizontal scroller on mobile --- */}
      <nav className="flex gap-1.5 overflow-x-auto rounded-full border border-border bg-surface p-1 lg:hidden">
        {TABS.map((t) => (
          <TabNavItem
            key={t.key}
            tab={t}
            status={statuses[t.key]}
            active={activeTab === t.key}
            href={`${base}?tab=${t.key}`}
            vertical={false}
          />
        ))}
      </nav>

      <div className="flex flex-1 items-start gap-6">
        <nav className="sticky top-20 hidden w-52 shrink-0 flex-col gap-1 rounded-xl border border-border bg-surface p-2 lg:flex">
          {TABS.map((t) => (
            <TabNavItem
              key={t.key}
              tab={t}
              status={statuses[t.key]}
              active={activeTab === t.key}
              href={`${base}?tab=${t.key}`}
              vertical={true}
            />
          ))}
        </nav>

        {/* --- Main content: the selected stage, full width --- */}
        <div className="min-w-0 flex-1">
          {activeTab === "trim" &&
            (sourceDuration !== undefined ? (
              <TrimEditor
                jobId={jobId}
                index={index}
                clip={clip}
                sourceDuration={sourceDuration}
                onClose={() => router.push("/history")}
                onUpdate={handleClipUpdate}
              />
            ) : (
              <p className="text-sm text-ink-muted">Source video info still loading…</p>
            ))}

          {activeTab === "frame" && (
            <FrameEditor
              jobId={jobId}
              index={index}
              clip={clip}
              onClose={() => router.push("/history")}
              onUpdate={handleClipUpdate}
            />
          )}

          {activeTab === "style" &&
            (framed ? (
              <div className="rounded-xl border border-border bg-surface p-4">
                <div className="flex items-center justify-between">
                  <p className="text-sm font-medium text-ink">Reference style</p>
                  <button
                    onClick={() => router.push("/history")}
                    className="text-xs text-ink-faint hover:text-ink-muted"
                  >
                    Close
                  </button>
                </div>
                <div className="mt-3">
                  <StylePanel jobId={jobId} index={index} clip={clip} onUpdate={handleClipUpdate} />
                </div>
              </div>
            ) : (
              <LockedStagePanel base={base} />
            ))}

          {activeTab === "captions" &&
            (framed ? (
              <CaptionEditor
                jobId={jobId}
                index={index}
                clip={clip}
                onClose={() => router.push("/history")}
                onUpdate={handleClipUpdate}
              />
            ) : (
              <LockedStagePanel base={base} />
            ))}

          {activeTab === "outro" &&
            (framed ? (
              <div className="rounded-xl border border-border bg-surface p-4">
                <div className="flex items-center justify-between">
                  <p className="text-sm font-medium text-ink">Outro</p>
                  <button
                    onClick={() => router.push("/history")}
                    className="text-xs text-ink-faint hover:text-ink-muted"
                  >
                    Close
                  </button>
                </div>
                <div className="mt-3">
                  <OutroPanel jobId={jobId} index={index} clip={clip} onUpdate={handleClipUpdate} />
                </div>
              </div>
            ) : (
              <LockedStagePanel base={base} />
            ))}
        </div>
      </div>
    </main>
  );
}

export default function ClipWorkspacePage() {
  return (
    <Suspense
      fallback={
        <main className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-4 px-6 py-10 sm:px-10">
          <p className="text-sm text-ink-muted">Loading…</p>
        </main>
      }
    >
      <ClipWorkspaceInner />
    </Suspense>
  );
}
