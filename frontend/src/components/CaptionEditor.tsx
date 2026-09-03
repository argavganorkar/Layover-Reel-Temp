"use client";

import { useRef, useState, useEffect } from "react";
import {
  CaptionBeat,
  CaptionRole,
  Clip,
  captionedReelDownloadUrl,
  fetchCaptionPlan,
  fetchCaptionPreviewHtml,
  generateCaptionPlan,
  reelDownloadUrl,
  saveCaptionPlan,
  startCaptionRender,
  styledReelDownloadUrl,
} from "@/lib/api";

// The three fixed roles - each has an entirely pre-set look (font, size,
// color) in the backend renderer. This is the only style choice left per
// beat; picking one is a one-click toggle, not a multi-field style editor.
const ROLES: { id: CaptionRole; label: string; hint: string }[] = [
  { id: "setup", label: "Setup", hint: "small, quiet serif" },
  { id: "punch", label: "Punch", hint: "big, bold sans" },
  { id: "accent", label: "Accent", hint: "rare blue script" },
];

// Mirrors caption_render.py's _ROLE_STYLE colors exactly, so the color
// picker shows the actual color a beat will render in even before any
// manual override is set.
const ROLE_DEFAULT_COLOR: Record<CaptionRole, string> = {
  setup: "#18140f",
  punch: "#18140f",
  accent: "#2b45e6",
};

// Mirrors caption_render.py's _DEFAULT_ANCHOR exactly, so a beat's default
// dot/handle position (before any manual drag) matches exactly where it
// actually renders - every beat uses the same fixed safe-zone spot unless
// dragged.
const DEFAULT_ANCHOR = { x: 0.5, y: 0.165 };

function formatClock(totalSeconds: number): string {
  const s = Math.max(0, totalSeconds);
  const m = Math.floor(s / 60);
  const sec = (s % 60).toFixed(1);
  return `${m}:${sec.padStart(4, "0")}`;
}

/** A small 9:16 preview swatch in the beat list - click to select that beat for on-canvas position/size editing above. Not itself draggable. */
function MiniPositionButton({ beat, active, onClick }: { beat: CaptionBeat; active: boolean; onClick: () => void }) {
  const isFree = beat.anchor_x != null || beat.anchor_y != null;
  const ax = beat.anchor_x ?? DEFAULT_ANCHOR.x;
  const ay = beat.anchor_y ?? DEFAULT_ANCHOR.y;
  return (
    <button
      type="button"
      onClick={onClick}
      title="Click to position &amp; size this caption on the preview above"
      className={`relative h-12 w-[27px] shrink-0 rounded-sm border bg-zinc-900 ${
        active ? "border-accent ring-1 ring-accent/50" : "border-border"
      }`}
    >
      <span
        className={`absolute h-1.5 w-1.5 -translate-x-1/2 -translate-y-1/2 rounded-full ${
          isFree ? "bg-amber-400" : "bg-zinc-500"
        }`}
        style={{ left: `${ax * 100}%`, top: `${ay * 100}%` }}
      />
    </button>
  );
}

/**
 * The draggable move dot + resize handle overlaid directly on the video
 * preview for whichever beat is currently selected - like dragging a text
 * box's position and corner handle in a video editor, rather than a
 * separate disconnected control.
 */
function CanvasHandle({
  beat,
  wrapperEl,
  onMove,
  onResize,
}: {
  beat: CaptionBeat;
  wrapperEl: HTMLDivElement;
  onMove: (ax: number, ay: number) => void;
  onResize: (sizeScale: number) => void;
}) {
  const movingRef = useRef(false);
  const resizingRef = useRef<{ startY: number; startScale: number } | null>(null);
  const ax = beat.anchor_x ?? DEFAULT_ANCHOR.x;
  const ay = beat.anchor_y ?? DEFAULT_ANCHOR.y;
  const sizeScale = beat.size_scale ?? 1;

  function moveFromEvent(e: React.PointerEvent) {
    const rect = wrapperEl.getBoundingClientRect();
    onMove(
      Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width)),
      Math.min(1, Math.max(0, (e.clientY - rect.top) / rect.height))
    );
  }

  // The resize handle sits a fixed offset up-and-right of the move dot;
  // dragging it up grows the caption, down shrinks it.
  const handleLeftPct = Math.min(96, ax * 100 + 9);
  const handleTopPct = Math.max(4, ay * 100 - 9);

  return (
    <>
      <div
        onPointerDown={(e) => {
          (e.target as Element).setPointerCapture(e.pointerId);
          movingRef.current = true;
          moveFromEvent(e);
        }}
        onPointerMove={(e) => movingRef.current && moveFromEvent(e)}
        onPointerUp={() => (movingRef.current = false)}
        onPointerCancel={() => (movingRef.current = false)}
        className="absolute z-10 h-4 w-4 -translate-x-1/2 -translate-y-1/2 cursor-move rounded-full border-2 border-white bg-blue-500 shadow"
        style={{ left: `${ax * 100}%`, top: `${ay * 100}%`, touchAction: "none" }}
        title="Drag to move this caption"
      />
      <div
        onPointerDown={(e) => {
          (e.target as Element).setPointerCapture(e.pointerId);
          resizingRef.current = { startY: e.clientY, startScale: sizeScale };
        }}
        onPointerMove={(e) => {
          if (!resizingRef.current) return;
          const rect = wrapperEl.getBoundingClientRect();
          const dy = (resizingRef.current.startY - e.clientY) / rect.height; // up = positive = bigger
          const next = Math.min(2.5, Math.max(0.4, resizingRef.current.startScale + dy * 3));
          onResize(Math.round(next * 100) / 100);
        }}
        onPointerUp={() => (resizingRef.current = null)}
        onPointerCancel={() => (resizingRef.current = null)}
        className="absolute z-10 h-3 w-3 -translate-x-1/2 -translate-y-1/2 cursor-ns-resize rounded-sm border-2 border-white bg-amber-400 shadow"
        style={{ left: `${handleLeftPct}%`, top: `${handleTopPct}%`, touchAction: "none" }}
        title="Drag up/down to resize this caption"
      />
    </>
  );
}

export default function CaptionEditor({
  jobId,
  index,
  clip,
  onClose,
  onUpdate,
}: {
  jobId: string;
  index: number;
  clip: Clip;
  onClose: () => void;
  onUpdate: (clip: Clip) => void;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const iframeRef = useRef<HTMLIFrameElement>(null);
  // A state setter (not a plain ref) as the ref callback, so the sizing
  // effect below re-runs exactly when this node actually mounts - it only
  // appears once `beats` loads, well after the component's first render.
  const [wrapperEl, setWrapperEl] = useState<HTMLDivElement | null>(null);
  const iframeReadyRef = useRef(false);

  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [beats, setBeats] = useState<CaptionBeat[] | null>(null);
  // A single beat index, the special value "all" (repositioning/resizing
  // every caption together), or null (nothing selected).
  const [selected, setSelected] = useState<number | "all" | null>(null);
  const [generating, setGenerating] = useState(false);
  const [genError, setGenError] = useState<string | null>(null);
  const [previewHtml, setPreviewHtml] = useState<string | null>(null);
  const [canvas, setCanvas] = useState({ width: 1080, height: 1920 });
  const [scale, setScale] = useState(1);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [rendering, setRendering] = useState(false);
  const [renderError, setRenderError] = useState<string | null>(null);
  // If the styled reel fails to actually load (backend hiccup, a stale
  // style_status pointing at a file that's since been removed, etc.), fall
  // back to the plain reel rather than leaving the preview stuck on a black,
  // unplayable video - see the <video onError> below.
  const [styledVideoFailed, setStyledVideoFailed] = useState(false);

  // --- Load the currently saved plan (if any) on open ---
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    fetchCaptionPlan(jobId, index)
      .then((res) => {
        if (cancelled) return;
        setBeats(res.beats);
      })
      .catch((e) => {
        if (cancelled) return;
        setLoadError(e instanceof Error ? e.message : "Could not load caption plan.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [jobId, index]);

  // Give the styled reel a fresh chance whenever the clip/style changes -
  // otherwise a failure recorded against an old style_status would
  // incorrectly keep suppressing a newly (re-)applied style's video too.
  useEffect(() => {
    setStyledVideoFailed(false);
  }, [jobId, index, clip.style_status]);

  // --- Keep the preview iframe scaled to fill the video's box, whatever
  // width this card happens to render at ---
  useEffect(() => {
    if (!wrapperEl) return;
    const update = () => setScale(wrapperEl.clientWidth / canvas.width);
    update();
    const ro = new ResizeObserver(update);
    ro.observe(wrapperEl);
    return () => ro.disconnect();
  }, [wrapperEl, canvas.width]);

  // --- Rebuild the preview HTML (debounced) whenever beats change - reuses
  // the exact same styling engine the real export uses (caption_render.py's
  // build_caption_html), so what's previewed is what gets burned in ---
  useEffect(() => {
    if (!beats) return;
    iframeReadyRef.current = false;
    const timer = setTimeout(() => {
      fetchCaptionPreviewHtml(jobId, index, beats)
        .then((res) => {
          setPreviewHtml(res.html);
          setCanvas(res.canvas);
        })
        .catch(() => {
          /* transient - preview will retry on the next beats change */
        });
    }, 350);
    return () => clearTimeout(timer);
  }, [jobId, index, beats]);

  function syncCaptionTime() {
    const v = videoRef.current;
    const win = iframeRef.current?.contentWindow as (Window & { setCaptionTime?: (t: number) => void }) | null;
    if (v && win?.setCaptionTime && iframeReadyRef.current) {
      win.setCaptionTime(v.currentTime);
    }
  }

  function handleIframeLoad() {
    iframeReadyRef.current = true;
    syncCaptionTime();
  }

  // Drive the caption overlay from the video's actual playback position -
  // rAF while playing (for smooth intro animations), plus timeupdate/seeked
  // to catch scrubbing via the native controls.
  useEffect(() => {
    let raf: number;
    const tick = () => {
      const v = videoRef.current;
      if (v && !v.paused && !v.seeking) syncCaptionTime();
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);

  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    v.addEventListener("seeked", syncCaptionTime);
    v.addEventListener("timeupdate", syncCaptionTime);
    return () => {
      v.removeEventListener("seeked", syncCaptionTime);
      v.removeEventListener("timeupdate", syncCaptionTime);
    };
  }, []);

  function updateBeat(i: number, patch: Partial<CaptionBeat>) {
    setBeats((prev) => (prev ? prev.map((b, bi) => (bi === i ? { ...b, ...patch } : b)) : prev));
    setDirty(true);
  }

  // Applies the same patch to every caption at once - used by "reposition
  // all captions" so one drag moves/resizes them all together, rather than
  // dragging each one individually. Since only one caption is ever on
  // screen at a time (see caption_render.py's activeBeatAt), giving them
  // all the identical anchor/size is exactly "move every caption the same
  // way", not a conflict.
  function updateAllBeats(patch: Partial<CaptionBeat>) {
    setBeats((prev) => (prev ? prev.map((b) => ({ ...b, ...patch })) : prev));
    setDirty(true);
  }

  function selectForCanvas(i: number) {
    setSelected((prev) => (prev === i ? null : i));
    const v = videoRef.current;
    const b = beats?.[i];
    if (v && b) v.currentTime = b.start;
  }

  function selectAllForCanvas() {
    setSelected((prev) => (prev === "all" ? null : "all"));
  }

  async function handleGenerate() {
    setGenerating(true);
    setGenError(null);
    try {
      const generated = await generateCaptionPlan(jobId, index);
      setBeats(generated);
      setSelected(null);
      setDirty(false);
    } catch (e) {
      setGenError(e instanceof Error ? e.message : "Could not generate captions.");
    } finally {
      setGenerating(false);
    }
  }

  async function handleSave() {
    if (!beats) return;
    setSaving(true);
    setSaveError(null);
    try {
      await saveCaptionPlan(jobId, index, beats);
      setDirty(false);
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : "Could not save changes.");
    } finally {
      setSaving(false);
    }
  }

  async function handleRender() {
    if (!beats) return;
    setRendering(true);
    setRenderError(null);
    try {
      if (dirty) {
        await saveCaptionPlan(jobId, index, beats);
        setDirty(false);
      }
      const updated = await startCaptionRender(jobId, index);
      onUpdate(updated);
    } catch (e) {
      setRenderError(e instanceof Error ? e.message : "Could not start rendering.");
    } finally {
      setRendering(false);
    }
  }

  const isCaptioning = clip.caption_status === "pending" || clip.caption_status === "running";
  // In "all" mode the handle needs *some* starting position to render at -
  // the first beat's current anchor/size stands in for the group. Once
  // anything is dragged, updateAllBeats gives every beat that same value,
  // so this stays an accurate read of "the group's" position from then on.
  const selectedBeat = selected === "all" ? beats?.[0] : selected !== null ? beats?.[selected] : undefined;

  return (
    <div className="mt-2 rounded-xl border border-border bg-surface p-4">
      <div className="flex items-center justify-between">
        <p className="text-xs font-medium text-ink">Captions for &ldquo;{clip.title}&rdquo;</p>
        <button onClick={onClose} className="text-xs text-ink-faint hover:text-ink-muted">
          Close
        </button>
      </div>

      {loading && <p className="mt-2 text-xs text-ink-muted">Loading…</p>}
      {loadError && <p className="mt-2 text-xs text-error">{loadError}</p>}

      {!loading && !beats && (
        <div className="mt-2">
          <p className="text-xs text-ink-muted">
            No caption plan yet. This asks the configured LLM to split this clip&rsquo;s words into
            short beats and tag each one setup, punch, or accent - a fixed, consistent look for
            each role, auto-placed in the same spot every time. No manual positioning required by
            default; you can still drag any individual caption below if you want it somewhere else.
          </p>
          {genError && <p className="mt-1 text-xs text-error">{genError}</p>}
          <button
            onClick={handleGenerate}
            disabled={generating}
            className="mt-2 rounded-md bg-accent-solid px-3 py-1.5 text-xs font-medium text-[#faf6f0] hover:bg-accent-solid-hover disabled:opacity-50"
          >
            {generating ? "Designing captions…" : "✨ Generate captions"}
          </button>
        </div>
      )}

      {!loading && beats && (
        <div className="mt-2">
          <p className="mb-2 text-xs text-ink-muted">
            Preview reflects your edits live. Click a caption below, then drag the blue dot on the
            preview to move it and the amber square to resize it - or use &ldquo;reposition all
            captions at once&rdquo; below the preview to move/resize every caption together in one
            drag.
          </p>

          <div
            ref={setWrapperEl}
            className="relative mx-auto aspect-[9/16] w-full max-w-[280px] overflow-hidden rounded-md bg-black"
          >
            <video
              ref={videoRef}
              // Mirrors the backend's storage.best_reel_input_path() exactly:
              // once a style has been applied, that's the base the export
              // actually burns captions onto - the preview should show the
              // same base, not the plain reel underneath it. Falls back to
              // the plain reel if the styled one fails to actually load
              // (backend down, file missing, etc.) rather than going black.
              src={
                clip.style_status === "done" && !styledVideoFailed
                  ? styledReelDownloadUrl(jobId, index)
                  : reelDownloadUrl(jobId, index)
              }
              onError={() => setStyledVideoFailed(true)}
              onLoadedData={(e) => {
                // Same fix as FrameEditor's video: a paused, never-played
                // video often paints nothing at all until a frame is
                // actually decoded - a tiny forced seek makes it paint
                // immediately, so the preview isn't blank before hitting
                // play.
                const v = e.currentTarget;
                if (v.currentTime === 0) v.currentTime = 0.01;
              }}
              controls
              preload="auto"
              crossOrigin="anonymous"
              className="absolute inset-0 h-full w-full"
            />
            {previewHtml !== null && (
              <iframe
                ref={iframeRef}
                title="Caption preview"
                srcDoc={previewHtml}
                onLoad={handleIframeLoad}
                className="pointer-events-none absolute left-0 top-0 border-0"
                style={{
                  width: canvas.width,
                  height: canvas.height,
                  transform: `scale(${scale})`,
                  transformOrigin: "0 0",
                }}
              />
            )}
            {selected !== null && wrapperEl && selectedBeat && (
              <CanvasHandle
                beat={selectedBeat}
                wrapperEl={wrapperEl}
                onMove={(ax, ay) =>
                  selected === "all"
                    ? updateAllBeats({ anchor_x: ax, anchor_y: ay })
                    : updateBeat(selected, { anchor_x: ax, anchor_y: ay })
                }
                onResize={(sizeScale) =>
                  selected === "all" ? updateAllBeats({ size_scale: sizeScale }) : updateBeat(selected, { size_scale: sizeScale })
                }
              />
            )}
          </div>

          <div className="mt-1.5">
            <button
              type="button"
              onClick={selectAllForCanvas}
              className={`text-[10px] font-medium ${
                selected === "all" ? "text-accent" : "text-accent hover:underline"
              }`}
            >
              {selected === "all" ? "✓ editing all captions together" : "🖱 reposition all captions at once"}
            </button>
          </div>

          {selected !== null && selectedBeat && (
            <div className="mt-1 flex flex-wrap items-center justify-between gap-x-3 gap-y-1 text-[10px] text-ink-muted">
              <span>
                {selected === "all"
                  ? "Editing all captions together - drag the blue dot to move every caption, amber square to resize them all."
                  : `Editing beat ${selected + 1} - drag the blue dot to move, amber square to resize.`}
              </span>
              <div className="flex items-center gap-2">
                <button
                  onClick={() =>
                    selected === "all"
                      ? updateAllBeats({ anchor_x: null, anchor_y: null })
                      : updateBeat(selected, { anchor_x: null, anchor_y: null })
                  }
                  className="text-accent hover:underline"
                >
                  reset position{selected === "all" ? " (all)" : ""}
                </button>
                <button
                  onClick={() =>
                    selected === "all" ? updateAllBeats({ size_scale: null }) : updateBeat(selected, { size_scale: null })
                  }
                  className="text-accent hover:underline"
                >
                  reset size{selected === "all" ? " (all)" : ""}
                </button>
                <button onClick={() => setSelected(null)} className="text-ink-faint hover:text-ink-muted">
                  done
                </button>
              </div>
            </div>
          )}

          <div className="mt-3 space-y-1.5">
            {beats.map((b, i) => (
              <div key={i} className="flex gap-2 rounded-md border border-border bg-surface-elevated p-2">
                <MiniPositionButton beat={b} active={selected === i} onClick={() => selectForCanvas(i)} />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-[10px] text-ink-faint">
                      {formatClock(b.start)}–{formatClock(b.end)}
                    </span>
                    <button
                      onClick={() => {
                        const v = videoRef.current;
                        if (v) v.currentTime = b.start;
                      }}
                      className="text-[10px] text-accent hover:underline"
                    >
                      ▶ jump here
                    </button>
                  </div>
                  <input
                    value={b.text}
                    onChange={(e) => updateBeat(i, { text: e.target.value })}
                    className="mt-1 w-full rounded border border-border bg-surface px-1.5 py-1 text-xs text-ink"
                  />
                  <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[10px]">
                    <div className="flex items-center gap-1">
                      {ROLES.map((r) => (
                        <button
                          key={r.id}
                          type="button"
                          onClick={() => updateBeat(i, { role: r.id })}
                          title={r.hint}
                          className={`rounded px-1.5 py-0.5 font-medium ${
                            b.role === r.id
                              ? "bg-accent-solid text-[#faf6f0]"
                              : "bg-surface text-ink-muted hover:bg-surface-hover"
                          }`}
                        >
                          {r.label}
                        </button>
                      ))}
                    </div>
                    <button
                      onClick={() => selectForCanvas(i)}
                      className={selected === i ? "font-medium text-accent" : "text-accent hover:underline"}
                    >
                      {selected === i ? "✓ editing position & size" : "🖱 position & size"}
                    </button>
                  </div>
                  <div className="mt-1.5 flex flex-wrap items-center gap-3 text-[10px]">
                    <label className="flex items-center gap-1 text-ink-muted">
                      Color
                      <input
                        type="color"
                        value={b.color ?? ROLE_DEFAULT_COLOR[b.role]}
                        onChange={(e) => updateBeat(i, { color: e.target.value })}
                        className="h-5 w-7 cursor-pointer rounded border border-border bg-surface p-0"
                        title="Text color for this caption"
                      />
                    </label>
                    {b.color && (
                      <button onClick={() => updateBeat(i, { color: null })} className="text-accent hover:underline">
                        reset color
                      </button>
                    )}
                    <label className="flex items-center gap-1.5 text-ink-muted">
                      Opacity
                      <input
                        type="range"
                        min={0.1}
                        max={1}
                        step={0.05}
                        value={b.opacity ?? 1}
                        onChange={(e) => updateBeat(i, { opacity: Number(e.target.value) })}
                        className="h-1.5 w-16 cursor-pointer accent-accent-solid"
                        title="Text opacity for this caption"
                      />
                      <span className="w-7 text-ink-faint">{Math.round((b.opacity ?? 1) * 100)}%</span>
                    </label>
                    {b.opacity != null && (
                      <button onClick={() => updateBeat(i, { opacity: null })} className="text-accent hover:underline">
                        reset opacity
                      </button>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>

          <div className="mt-3 flex flex-wrap items-center gap-2">
            <button
              onClick={handleGenerate}
              disabled={generating}
              className="rounded-md bg-surface-elevated px-2.5 py-1 text-xs font-medium text-ink hover:bg-surface-hover disabled:opacity-50"
            >
              {generating ? "Regenerating…" : "↻ Regenerate"}
            </button>
            <button
              onClick={handleSave}
              disabled={saving || !dirty}
              className="rounded-md bg-surface-elevated px-2.5 py-1 text-xs font-medium text-ink hover:bg-surface-hover disabled:opacity-50"
            >
              {saving ? "Saving…" : "Save changes"}
            </button>
            <button
              onClick={handleRender}
              disabled={rendering || isCaptioning}
              className="rounded-md bg-accent-solid px-3 py-1.5 text-xs font-medium text-[#faf6f0] hover:bg-accent-solid-hover disabled:opacity-50"
            >
              {rendering || isCaptioning ? "Rendering…" : "Render captions"}
            </button>
          </div>
          {genError && <p className="mt-1 text-xs text-error">{genError}</p>}
          {saveError && <p className="mt-1 text-xs text-error">{saveError}</p>}
          {renderError && <p className="mt-1 text-xs text-error">{renderError}</p>}
          {isCaptioning && (
            <p className="mt-1 flex items-center gap-1.5 text-xs text-accent">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent" />
              Rendering captions (headless browser + ffmpeg - this can take a bit longer than
              framing did)…
            </p>
          )}
          {clip.caption_status === "error" && (
            <p className="mt-1 text-xs text-error">Caption render failed: {clip.caption_error}</p>
          )}
          {clip.caption_status === "done" && (
            <a
              href={captionedReelDownloadUrl(jobId, index)}
              download
              target="_blank"
              rel="noopener noreferrer"
              className="mt-2 inline-block rounded-md bg-success/10 px-2.5 py-1 text-xs font-medium text-success hover:bg-success/20"
            >
              Download captioned reel ↓
            </a>
          )}
        </div>
      )}
    </div>
  );
}
