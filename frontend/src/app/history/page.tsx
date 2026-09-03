"use client";

import ResultsGallery from "@/components/ResultsGallery";
import JobsList from "@/components/JobsList";
import { useJobsPolling } from "@/lib/hooks";

export default function HistoryPage() {
  const { jobs, loadError, refresh } = useJobsPolling();

  return (
    <main className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-8 px-6 py-10 sm:px-10">
      <header>
        <h1 className="font-serif text-3xl font-medium tracking-tight text-ink">History</h1>
        <p className="mt-1.5 text-sm text-ink-muted">
          Every upload, its clips, and everything made from them - framing, style, captions, and
          downloads all happen here.
        </p>
      </header>

      {loadError && (
        <div className="rounded-xl border border-warning/30 bg-warning/10 p-4 text-sm text-warning">
          {loadError}
        </div>
      )}

      <ResultsGallery jobs={jobs} />

      <JobsList jobs={jobs} onAction={refresh} title="All uploads" emptyMessage="No videos uploaded yet." />
    </main>
  );
}
