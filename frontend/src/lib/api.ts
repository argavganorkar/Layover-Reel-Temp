// Small client for talking to the FastAPI backend.
// Phase 1 only needs upload + list/get job. Later phases add more calls here.

export const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export type JobStatus =
  | "uploaded"
  | "transcribing"
  | "finding_moments"
  | "cutting"
  | "reframing"
  | "captioning"
  | "rendering"
  | "complete"
  | "error";

export interface MediaInfo {
  duration_seconds: number;
  width: number;
  height: number;
  fps: number;
  video_codec: string | null;
  has_audio: boolean;
  audio_codec: string | null;
  size_bytes: number;
}

export type ProbeStatus = "pending" | "running" | "done" | "error" | undefined;

export interface Job {
  id: string;
  original_filename: string;
  stored_filename: string;
  size_bytes: number;
  status: JobStatus;
  error: string | null;
  created_at: string;
  updated_at: string;
  probe_status?: ProbeStatus;
  probe_error?: string | null;
  media_info?: MediaInfo;
  audio_extracted?: boolean;
  transcript_status?: "pending" | "running" | "done" | "error";
  transcript_progress?: number;
  transcript_error?: string | null;
  transcript_summary?: {
    language: string;
    duration_seconds: number;
    segment_count: number;
    word_count: number;
  };
  clips_status?: "pending" | "running" | "done" | "error";
  clips_error?: string | null;
  clips_count?: number;
  cut_status?: "pending" | "running" | "done" | "error";
  cut_error?: string | null;
}

export function formatDuration(totalSeconds: number): string {
  const s = Math.round(totalSeconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h}h ${m}m ${sec}s`;
  if (m > 0) return `${m}m ${sec}s`;
  return `${sec}s`;
}

export async function fetchJobs(): Promise<Job[]> {
  const res = await fetch(`${API_URL}/api/jobs`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Failed to fetch jobs (${res.status})`);
  const data = await res.json();
  return data.jobs;
}

export async function fetchJob(jobId: string): Promise<Job> {
  const res = await fetch(`${API_URL}/api/jobs/${jobId}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Failed to fetch job (${res.status})`);
  const data = await res.json();
  return data.job;
}

export async function deleteJob(jobId: string): Promise<void> {
  const res = await fetch(`${API_URL}/api/jobs/${jobId}`, { method: "DELETE" });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || `Failed to delete (${res.status})`);
  }
}

export async function startTranscription(jobId: string): Promise<Job> {
  const res = await fetch(`${API_URL}/api/jobs/${jobId}/transcribe`, { method: "POST" });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || `Failed to start transcription (${res.status})`);
  }
  const data = await res.json();
  return data.job;
}

export type ContentPreference =
  | "best"
  | "educational"
  | "funny"
  | "storytelling"
  | "controversial"
  | "emotional";

export interface ClipRequest {
  num_clips: 3 | 5 | 10;
  target_length_seconds: 30 | 45 | 60;
  content_preference: ContentPreference;
}

export interface Clip {
  start: number;
  end: number;
  title: string;
  hook: string;
  reason: string;
  score: number;
  cut_status?: "pending" | "running" | "done" | "error";
  cut_error?: string | null;
  output_filename?: string;
  reframe_status?: "pending" | "running" | "done" | "error";
  reframe_error?: string | null;
  reel_filename?: string;
  caption_status?: "pending" | "running" | "done" | "error";
  caption_error?: string | null;
  captioned_reel_filename?: string;
  style_status?: "pending" | "running" | "done" | "error" | null;
  style_error?: string | null;
  styled_reel_filename?: string | null;
  style_started_at?: string | null;
  style_frames_done?: number | null;
  style_frames_total?: number | null;
  outro_status?: "pending" | "running" | "done" | "error" | null;
  outro_error?: string | null;
  outro_reel_filename?: string | null;
}

// --- Phase 6: 9:16 reframing ---

export type FrameLayout = "vertical" | "free" | "centered" | "spotlight" | "split" | "trio" | "horizontal";
export type FrameMode = "crop" | "letterbox";

export interface FrameBox {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface FramePosition {
  start: number;
  end: number;
  layout: FrameLayout;
  mode: FrameMode;
  boxes: FrameBox[];
}

export interface FramePlan {
  canvas: { width: number; height: number };
  positions: FramePosition[];
}

export async function fetchFramePlan(
  jobId: string,
  index: number
): Promise<{ frame_plan: FramePlan; is_default: boolean; source_width: number; source_height: number }> {
  const res = await fetch(`${API_URL}/api/jobs/${jobId}/clips/${index}/frame-plan`, {
    cache: "no-store",
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || `Failed to fetch frame plan (${res.status})`);
  }
  return res.json();
}

export async function startReframe(
  jobId: string,
  index: number,
  positions: FramePosition[],
  canvas?: { width: number; height: number }
): Promise<Clip> {
  const res = await fetch(`${API_URL}/api/jobs/${jobId}/clips/${index}/reframe`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ positions, canvas }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || `Failed to start reframing (${res.status})`);
  }
  const data = await res.json();
  return data.clip;
}

export function reelDownloadUrl(jobId: string, index: number): string {
  return `${API_URL}/api/jobs/${jobId}/clips/${index}/reel`;
}

export async function startClipSelection(jobId: string, req: ClipRequest): Promise<Job> {
  const res = await fetch(`${API_URL}/api/jobs/${jobId}/select-clips`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || `Failed to start clip selection (${res.status})`);
  }
  const data = await res.json();
  return data.job;
}

export async function fetchClips(jobId: string): Promise<Clip[]> {
  const res = await fetch(`${API_URL}/api/jobs/${jobId}/clips`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Failed to fetch clips (${res.status})`);
  const data = await res.json();
  return data.clips;
}

export async function startCutting(jobId: string): Promise<Job> {
  const res = await fetch(`${API_URL}/api/jobs/${jobId}/cut-clips`, { method: "POST" });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || `Failed to start cutting (${res.status})`);
  }
  const data = await res.json();
  return data.job;
}

export function clipDownloadUrl(jobId: string, index: number): string {
  return `${API_URL}/api/jobs/${jobId}/clips/${index}/file`;
}

// --- Phase 6.5: adjust a clip's start/end timing after the AI picked it ---

export function sourceFileUrl(jobId: string): string {
  return `${API_URL}/api/jobs/${jobId}/source-file`;
}

export async function trimClip(
  jobId: string,
  index: number,
  start: number,
  end: number
): Promise<Clip> {
  const res = await fetch(`${API_URL}/api/jobs/${jobId}/clips/${index}/trim`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ start, end }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || `Failed to adjust timing (${res.status})`);
  }
  const data = await res.json();
  return data.clip;
}

// --- Phase 8: fixed three-tier typography captions ---
//
// A beat's whole visual treatment (font, size, color, position, animation)
// is derived from its `role` alone by the backend renderer - the frontend
// never computes style, it just previews the HTML the backend builds and
// lets the user pick a role + optionally drag a manual position/size
// override on top of the fixed default.

export type CaptionRole = "setup" | "punch" | "accent";

export interface CaptionBeat {
  text: string;
  role: CaptionRole;
  // Explicit free-position override, 0-1 normalized. null/omitted = the
  // fixed default safe-zone anchor every beat uses out of the box. Set only
  // by dragging in the caption editor - the LLM never sets this.
  anchor_x?: number | null;
  anchor_y?: number | null;
  // Manual size-multiplier override (0.4-2.5x), set by dragging the
  // on-canvas resize handle. null/omitted = the role's fixed default size.
  size_scale?: number | null;
  // Manual text-color override, a 6-digit hex string (e.g. "#ffcc00") from a
  // native <input type="color">. null/omitted = the role's fixed default
  // color. The LLM never sets this - manual override only, same pattern as
  // anchor_x/anchor_y/size_scale.
  color?: string | null;
  // Manual opacity override, 0.1-1.0. null/omitted = fully opaque (1.0).
  opacity?: number | null;
  // Derived from real ASR word timing server-side, clip-relative seconds.
  start: number;
  end: number;
}

export async function fetchCaptionPlan(
  jobId: string,
  index: number
): Promise<{ beats: CaptionBeat[] | null }> {
  const res = await fetch(`${API_URL}/api/jobs/${jobId}/clips/${index}/caption-plan`, {
    cache: "no-store",
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || `Failed to fetch caption plan (${res.status})`);
  }
  const data = await res.json();
  return data;
}

export async function generateCaptionPlan(jobId: string, index: number): Promise<CaptionBeat[]> {
  const res = await fetch(`${API_URL}/api/jobs/${jobId}/clips/${index}/caption-plan/generate`, {
    method: "POST",
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || `Failed to generate captions (${res.status})`);
  }
  const data = await res.json();
  return data.beats;
}

export async function saveCaptionPlan(
  jobId: string,
  index: number,
  beats: CaptionBeat[]
): Promise<CaptionBeat[]> {
  const res = await fetch(`${API_URL}/api/jobs/${jobId}/clips/${index}/caption-plan`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ beats }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || `Failed to save caption plan (${res.status})`);
  }
  const data = await res.json();
  return data.beats;
}

export async function fetchCaptionPreviewHtml(
  jobId: string,
  index: number,
  beats: CaptionBeat[]
): Promise<{ html: string; canvas: { width: number; height: number } }> {
  const res = await fetch(`${API_URL}/api/jobs/${jobId}/clips/${index}/caption-plan/preview-html`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ beats }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || `Failed to build caption preview (${res.status})`);
  }
  return res.json();
}

export async function startCaptionRender(jobId: string, index: number): Promise<Clip> {
  const res = await fetch(`${API_URL}/api/jobs/${jobId}/clips/${index}/captions/render`, {
    method: "POST",
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || `Failed to start caption rendering (${res.status})`);
  }
  const data = await res.json();
  return data.clip;
}

export function captionedReelDownloadUrl(jobId: string, index: number): string {
  return `${API_URL}/api/jobs/${jobId}/clips/${index}/captioned-reel`;
}

// --- Phase 10: reference-style "visual DNA" preset ---

export async function startStyleRender(jobId: string, index: number): Promise<Clip> {
  const res = await fetch(`${API_URL}/api/jobs/${jobId}/clips/${index}/style/render`, {
    method: "POST",
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || `Failed to start applying the style (${res.status})`);
  }
  const data = await res.json();
  return data.clip;
}

export async function clearStyle(jobId: string, index: number): Promise<Clip> {
  const res = await fetch(`${API_URL}/api/jobs/${jobId}/clips/${index}/style`, {
    method: "DELETE",
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || `Failed to remove the style (${res.status})`);
  }
  const data = await res.json();
  return data.clip;
}

export function styledReelDownloadUrl(jobId: string, index: number): string {
  return `${API_URL}/api/jobs/${jobId}/clips/${index}/styled-reel`;
}

// --- Add-outro: appends the bundled outro clip to the clip's most-finished reel ---

export async function startOutroRender(jobId: string, index: number): Promise<Clip> {
  const res = await fetch(`${API_URL}/api/jobs/${jobId}/clips/${index}/outro`, {
    method: "POST",
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || `Failed to add the outro (${res.status})`);
  }
  const data = await res.json();
  return data.clip;
}

export async function clearOutro(jobId: string, index: number): Promise<Clip> {
  const res = await fetch(`${API_URL}/api/jobs/${jobId}/clips/${index}/outro`, {
    method: "DELETE",
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || `Failed to remove the outro (${res.status})`);
  }
  const data = await res.json();
  return data.clip;
}

export function outroReelDownloadUrl(jobId: string, index: number): string {
  return `${API_URL}/api/jobs/${jobId}/clips/${index}/outro-reel`;
}

/**
 * Uploads a video with progress reporting. Uses XMLHttpRequest instead of
 * fetch() because fetch has no reliable upload-progress event, and a
 * 1-2 hour video upload needs a visible progress bar.
 */
export function uploadVideo(
  file: File,
  onProgress: (percent: number) => void
): Promise<Job> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_URL}/api/upload`);

    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        onProgress(Math.round((event.loaded / event.total) * 100));
      }
    };

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          const data = JSON.parse(xhr.responseText);
          resolve(data.job as Job);
        } catch {
          reject(new Error("Server returned an unexpected response."));
        }
      } else {
        try {
          const data = JSON.parse(xhr.responseText);
          reject(new Error(data.detail || `Upload failed (${xhr.status})`));
        } catch {
          reject(new Error(`Upload failed (${xhr.status})`));
        }
      }
    };

    xhr.onerror = () => reject(new Error("Network error during upload. Is the backend running?"));

    const formData = new FormData();
    formData.append("file", file);
    xhr.send(formData);
  });
}

export function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return `${(bytes / Math.pow(1024, i)).toFixed(1)} ${units[i]}`;
}

// Ordered pipeline stages, used to render the progress UI in later phases.
export const STATUS_STEPS: { key: JobStatus; label: string }[] = [
  { key: "uploaded", label: "Uploading" },
  { key: "transcribing", label: "Transcribing" },
  { key: "finding_moments", label: "Finding moments" },
  { key: "cutting", label: "Cutting clips" },
  { key: "reframing", label: "Reframing" },
  { key: "captioning", label: "Generating captions" },
  { key: "rendering", label: "Rendering" },
  { key: "complete", label: "Complete" },
];
