"use client";

import { useCallback, useEffect, useState } from "react";
import { Job, fetchJobs } from "./api";

function hasInFlightWork(jobs: Job[]): boolean {
  return jobs.some(
    (j) =>
      j.probe_status === "pending" ||
      j.probe_status === "running" ||
      j.transcript_status === "pending" ||
      j.transcript_status === "running" ||
      j.clips_status === "pending" ||
      j.clips_status === "running" ||
      j.cut_status === "pending" ||
      j.cut_status === "running"
  );
}

/**
 * Shared "load every job, then poll while anything's in flight" logic - used
 * by both the Workspace and History screens so there's exactly one copy of
 * this instead of two screens drifting apart.
 */
export function useJobsPolling() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const data = await fetchJobs();
      setJobs(data);
      setLoadError(null);
    } catch (e) {
      setLoadError(
        e instanceof Error
          ? `${e.message}. Make sure the backend server is running on port 8000.`
          : "Could not reach the backend."
      );
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetchJobs()
      .then((data) => {
        if (!cancelled) {
          setJobs(data);
          setLoadError(null);
        }
      })
      .catch((e) => {
        if (!cancelled) {
          setLoadError(
            e instanceof Error
              ? `${e.message}. Make sure the backend server is running on port 8000.`
              : "Could not reach the backend."
          );
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!hasInFlightWork(jobs)) return;
    const interval = setInterval(refresh, 2000);
    return () => clearInterval(interval);
  }, [jobs, refresh]);

  function addJob(job: Job) {
    setJobs((prev) => [job, ...prev]);
  }

  return { jobs, loading, loadError, refresh, addJob };
}
