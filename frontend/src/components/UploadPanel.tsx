"use client";

import { useRef, useState } from "react";
import { uploadVideo, Job } from "@/lib/api";
import ProgressBar from "./ProgressBar";

interface Props {
  onUploaded: (job: Job) => void;
}

export default function UploadPanel({ onUploaded }: Props) {
  const [dragActive, setDragActive] = useState(false);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const isUploading = progress !== null && progress < 100;

  function pickFile(file: File | null) {
    setError(null);
    setSelectedFile(file);
  }

  async function handleUpload() {
    if (!selectedFile) return;
    setError(null);
    setProgress(0);
    try {
      const job = await uploadVideo(selectedFile, setProgress);
      setProgress(100);
      onUploaded(job);
      if (inputRef.current) inputRef.current.value = "";
      // Leave the "Upload complete" state visible briefly instead of
      // clearing it in the same tick (which would make it flash and vanish).
      setTimeout(() => {
        setSelectedFile(null);
        setProgress(null);
      }, 1200);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Upload failed.");
      setProgress(null);
    }
  }

  return (
    <div className="shadow-warm h-fit w-full rounded-2xl border border-border bg-surface p-6">
      <h2 className="font-serif text-lg font-medium text-ink">New upload</h2>
      <p className="mt-1 text-sm text-ink-muted">
        A long-form video (podcast, interview, talk). MP4, MOV, MKV, WEBM, AVI or M4V.
      </p>

      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragActive(true);
        }}
        onDragLeave={() => setDragActive(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragActive(false);
          const file = e.dataTransfer.files?.[0];
          if (file) pickFile(file);
        }}
        onClick={() => inputRef.current?.click()}
        className={`mt-4 flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed px-6 py-10 text-center transition-colors ${
          dragActive ? "border-accent bg-accent/10" : "border-border bg-surface-hover hover:bg-surface-elevated"
        }`}
      >
        <input
          ref={inputRef}
          type="file"
          accept=".mp4,.mov,.mkv,.webm,.avi,.m4v,video/*"
          className="hidden"
          onChange={(e) => pickFile(e.target.files?.[0] ?? null)}
        />
        {selectedFile ? (
          <div>
            <p className="font-medium text-ink">{selectedFile.name}</p>
            <p className="text-sm text-ink-muted">{(selectedFile.size / (1024 * 1024)).toFixed(1)} MB</p>
          </div>
        ) : (
          <>
            <span className="mb-2 flex h-10 w-10 items-center justify-center rounded-full border border-border bg-surface text-ink-muted">
              ↑
            </span>
            <p className="text-sm text-ink-muted">
              Drag &amp; drop a video here, or <span className="text-accent underline">browse</span>
            </p>
          </>
        )}
      </div>

      {error && <p className="mt-3 text-sm text-error">{error}</p>}

      {progress !== null && (
        <div className="mt-4">
          <ProgressBar percent={progress} label={progress < 100 ? "Uploading…" : "Upload complete ✓"} />
        </div>
      )}

      <button
        onClick={handleUpload}
        disabled={!selectedFile || isUploading}
        className="mt-4 w-full rounded-lg bg-accent-solid px-4 py-2.5 text-sm font-medium text-[#faf6f0] transition-colors hover:bg-accent-solid-hover disabled:cursor-not-allowed disabled:bg-surface-elevated disabled:text-ink-faint"
      >
        {isUploading ? "Uploading…" : "Upload video"}
      </button>
    </div>
  );
}
