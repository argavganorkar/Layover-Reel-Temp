/**
 * One visual progress bar, used everywhere something is running: uploads,
 * transcription, and reference-style rendering pass a real `percent`;
 * everything else (clip selection, cutting, reframing, caption/outro
 * rendering) has no granular progress from the backend, so it renders as an
 * "indeterminate" shimmer instead of faking a percentage. Either way this
 * replaces scattered plain-text "X% remaining" lines with one consistent bar.
 */
export default function ProgressBar({
  percent,
  label,
  tone = "accent",
}: {
  /** 0-100, or omit/undefined for an indeterminate (unknown-length) bar. */
  percent?: number | null;
  label?: string;
  tone?: "accent" | "success" | "warning";
}) {
  const determinate = percent !== undefined && percent !== null && Number.isFinite(percent);
  const clamped = determinate ? Math.min(100, Math.max(0, percent as number)) : 0;
  const barColor = tone === "success" ? "bg-success" : tone === "warning" ? "bg-warning" : "bg-accent-solid";
  const dotColor = tone === "success" ? "bg-success" : tone === "warning" ? "bg-warning" : "bg-accent";

  return (
    <div className="w-full">
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-surface-elevated">
        {determinate ? (
          <div
            className={`h-full rounded-full ${barColor} transition-[width] duration-500 ease-out`}
            style={{ width: `${clamped}%` }}
          />
        ) : (
          <div className={`h-full w-1/3 rounded-full ${barColor} animate-[progress-indeterminate_1.3s_ease-in-out_infinite]`} />
        )}
      </div>
      {label && (
        <p className="mt-1 flex items-center gap-1.5 text-xs text-ink-muted">
          <span className={`h-1.5 w-1.5 shrink-0 animate-pulse rounded-full ${dotColor}`} />
          {label}
          {determinate && <span className="ml-auto shrink-0 font-medium text-ink">{Math.round(clamped)}%</span>}
        </p>
      )}
    </div>
  );
}
