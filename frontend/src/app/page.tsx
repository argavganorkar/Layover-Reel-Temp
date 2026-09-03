"use client";

import Link from "next/link";
import UploadPanel from "@/components/UploadPanel";
import JobsList from "@/components/JobsList";
import { useJobsPolling } from "@/lib/hooks";
import { Job } from "@/lib/api";

/** A job still belongs on the Workspace screen while it's on its way to
 * having cut clips - everything after that (framing, style, captions,
 * outro, downloads) is per-clip creative work that lives in History, so
 * Home doesn't turn into an ever-growing list of every video ever
 * uploaded. */
function isActiveJob(job: Job): boolean {
  return job.clips_status !== "done" || job.cut_status !== "done";
}

export default function Home() {
  const { jobs, loadError, refresh, addJob } = useJobsPolling();
  const activeJobs = jobs.filter(isActiveJob);
  const settledCount = jobs.length - activeJobs.length;

  return (
    <main className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-8 px-6 py-10 sm:px-10">
      <header>
        <h1 className="font-serif text-3xl font-medium tracking-tight text-ink">Workspace</h1>
        <p className="mt-1.5 text-sm text-ink-muted">
          Upload a long video and get it ready as clips. Once a clip&apos;s cut, everything else -
          framing, style, captions, downloads - happens in{" "}
          <Link href="/history" className="text-accent hover:underline">
            History
          </Link>
          .
        </p>
      </header>

      {loadError && (
        <div className="rounded-xl border border-warning/30 bg-warning/10 p-4 text-sm text-warning">
          {loadError}
        </div>
      )}

      <div className="grid grid-cols-1 gap-8 lg:grid-cols-[minmax(0,26rem)_1fr]">
        <UploadPanel onUploaded={addJob} />

        <div className="min-w-0">
          {activeJobs.length > 0 ? (
            <JobsList
              jobs={activeJobs}
              onAction={refresh}
              title="In progress"
              emptyMessage="Nothing in progress - upload a video to get started."
            />
          ) : (
            <div className="flex h-full min-h-[220px] flex-col items-center justify-center rounded-2xl border border-dashed border-border bg-surface/40 p-8 text-center">
              <p className="text-sm font-medium text-ink">Nothing in progress right now.</p>
              <p className="mt-1 text-sm text-ink-muted">
                {settledCount > 0
                  ? "Every upload has its clips cut and ready. Pick up framing, style, captions, or an outro from "
                  : "Upload a video on the left to get started."}
                {settledCount > 0 && (
                  <>
                    {" "}
                    <Link href="/history" className="text-accent hover:underline">
                      History
                    </Link>
                    .
                  </>
                )}
              </p>
            </div>
          )}
        </div>
      </div>
    </main>
  );
}
