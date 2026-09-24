/**
 * Studio: the home of the product. Every production the pipeline is making or has made, what stage
 * it's at, its script, footage (with licences), narration and renders. "Open in editor" turns it
 * into a real timeline in the editor.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  ArrowRight,
  Check,
  Circle,
  Clapperboard,
  ExternalLink,
  FileText,
  Film,
  Image as ImageIcon,
  Layers,
  ListChecks,
  Loader2,
  Mic,
  Play,
  Scissors,
  X,
} from "@/icons/lucide-compat";
import { useRouter } from "../hooks/use-router";
import { BRAND } from "./brand";
import { buildTimeline } from "./build-timeline";
import {
  fetchIndex,
  fetchProduction,
  feedUrl,
  fmtAgo,
  fmtBytes,
  fmtDuration,
  usePolled,
  type Beat,
  type MediaAsset,
  type Production,
  type ProductionSummary,
  type Stage,
} from "./feed";

type Tab = "watch" | "script" | "footage" | "activity";

const STATE_STYLE: Record<ProductionSummary["state"], string> = {
  draft: "bg-bg-3 text-fg-2",
  running: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  ready: "bg-sky-500/15 text-sky-700 dark:text-sky-300",
  done: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  failed: "bg-red-500/15 text-red-700 dark:text-red-300",
};

export function StudioHub({ initialSlug }: { initialSlug?: string }) {
  const { navigate } = useRouter();
  const index = usePolled(fetchIndex, (d) => d.productions.some((p) => p.state === "running"));
  const productions = index.data?.productions ?? [];
  const [picked, setPicked] = useState<string | undefined>(initialSlug);
  const slug = picked ?? productions[0]?.slug;

  return (
    <div className="flex h-screen w-screen flex-col bg-bg text-fg">
      <header className="flex h-14 shrink-0 items-center gap-4 border-b border-border bg-bg-1 px-5">
        <Clapperboard size={20} className="text-accent" aria-hidden />
        <div className="flex min-w-0 flex-col leading-tight">
          <span className="text-sm font-semibold tracking-wide">{BRAND.name}</span>
          <span className="truncate text-xs text-fg-muted">{BRAND.tagline}</span>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <button
            type="button"
            onClick={() => navigate("welcome")}
            className="rounded-md border border-border px-3 py-1.5 text-xs text-fg-2 hover:bg-hover"
          >
            Blank editor
          </button>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        <aside className="flex w-72 shrink-0 flex-col border-r border-border bg-bg-1">
          <div className="px-4 pb-2 pt-4 text-[11px] font-semibold uppercase tracking-wider text-fg-muted">
            Productions
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-4">
            {productions.map((p) => (
              <ProductionRow key={p.slug} p={p} active={p.slug === slug} onClick={() => setPicked(p.slug)} />
            ))}
            {!index.data && !index.error && <div className="px-3 py-6 text-xs text-fg-muted">Loading…</div>}
          </div>
          <div className="border-t border-border px-4 py-3 text-[11px] leading-relaxed text-fg-muted">
            {BRAND.poweredBy}
          </div>
        </aside>

        <main className="min-w-0 flex-1 overflow-y-auto">
          {slug ? (
            <ProductionView key={slug} slug={slug} />
          ) : (
            <EmptyFeed error={index.error} loaded={!!index.data} />
          )}
        </main>
      </div>
    </div>
  );
}

function ProductionRow({ p, active, onClick }: { p: ProductionSummary; active: boolean; onClick: () => void }) {
  const [done, total] = p.progress;
  return (
    <button
      type="button"
      onClick={onClick}
      className={`mb-1 flex w-full gap-3 rounded-lg p-2 text-left transition-colors ${
        active ? "bg-selected" : "hover:bg-hover"
      }`}
    >
      <div className="relative h-12 w-20 shrink-0 overflow-hidden rounded bg-bg-3">
        {p.thumb && <img src={feedUrl(p.thumb)} alt="" className="h-full w-full object-cover" />}
        {p.durationSec ? (
          <span className="absolute bottom-0.5 right-0.5 rounded bg-black/70 px-1 text-[10px] text-white">
            {fmtDuration(p.durationSec)}
          </span>
        ) : null}
      </div>
      <div className="min-w-0 flex-1">
        <div className="truncate text-[13px] font-medium">{p.title}</div>
        <div className="mt-1 flex items-center gap-1.5">
          <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium capitalize ${STATE_STYLE[p.state]}`}>
            {p.state === "running" && p.stage ? p.stage : p.state}
          </span>
          <span className="text-[10px] text-fg-muted">{fmtAgo(p.updatedAt)}</span>
        </div>
        <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-bg-3">
          <div className="h-full rounded-full bg-accent" style={{ width: `${(done / Math.max(1, total)) * 100}%` }} />
        </div>
      </div>
    </button>
  );
}

function EmptyFeed({ error, loaded }: { error: string | null; loaded: boolean }) {
  return (
    <div className="mx-auto max-w-xl px-8 py-20 text-center">
      <Clapperboard size={36} className="mx-auto mb-4 text-fg-muted" aria-hidden />
      <h2 className="mb-2 text-lg font-semibold">{loaded ? "No productions yet" : "Waiting for the pipeline"}</h2>
      <p className="mb-6 text-sm text-fg-2">
        Productions appear here as soon as the pipeline publishes them. Each one shows its stages, script,
        footage and renders live.
      </p>
      <pre className="rounded-lg bg-bg-2 p-4 text-left text-xs text-fg-2">
        {`python examples/make_demo.py projects/demo
python -m worker.worker --local projects/demo \\
  --out out/demo --feed editor/apps/web/public/studio-feed`}
      </pre>
      {error && <p className="mt-4 text-xs text-fg-muted">Feed: {error}</p>}
    </div>
  );
}

// ── one production ──────────────────────────────────────────────────────────────────────────────
function ProductionView({ slug }: { slug: string }) {
  const load = useCallback(() => fetchProduction(slug), [slug]);
  const { data: p, error } = usePolled(load, (d) => d.stages.some((s) => s.status === "running"));
  const [tab, setTab] = useState<Tab>("watch");
  const [seek, setSeek] = useState<{ t: number; n: number } | null>(null);
  const [building, setBuilding] = useState<{ msg: string; f: number; error?: string } | null>(null);
  const { navigate } = useRouter();

  const openInEditor = async () => {
    if (!p) return;
    setBuilding({ msg: "Starting…", f: 0 });
    try {
      const warnings = await buildTimeline(p, (msg, f) => setBuilding({ msg, f }));
      if (warnings.length) console.warn("[studio] timeline warnings", warnings);
      navigate("editor");
    } catch (e) {
      setBuilding({ msg: "Couldn't build the timeline", f: 1, error: e instanceof Error ? e.message : String(e) });
    }
  };

  if (!p) {
    return <div className="p-10 text-sm text-fg-muted">{error ? `Couldn't load: ${error}` : "Loading…"}</div>;
  }
  const beats = p.script.chapters.reduce((n, c) => n + c.beats.length, 0);
  const output = p.outputs.find((o) => o.kind === "final") ?? p.outputs[0];

  return (
    <div className="mx-auto max-w-6xl px-8 pb-16 pt-7">
      <div className="flex flex-wrap items-start gap-4">
        <div className="min-w-0 flex-1">
          <h1 className="text-2xl font-semibold tracking-tight">{p.title}</h1>
          {p.topic && <p className="mt-1 max-w-3xl text-sm text-fg-2">{p.topic}</p>}
          <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-xs text-fg-muted">
            <span>{p.script.chapters.length} chapters · {beats} beats</span>
            <span>{p.media.length} media</span>
            {p.narration && <span>Narration {fmtDuration(p.narration.durationSec)}{p.voice?.voice ? ` · ${p.voice.voice}` : ""}</span>}
            <span>Updated {fmtAgo(p.updatedAt)}</span>
          </div>
        </div>
        <button
          type="button"
          onClick={openInEditor}
          disabled={!!building && !building.error}
          className="flex items-center gap-2 rounded-lg bg-accent px-4 py-2.5 text-sm font-medium text-accent-fg hover:bg-accent-strong disabled:opacity-60"
        >
          <Scissors size={15} aria-hidden /> Open in editor
        </button>
      </div>

      <StageTracker stages={p.stages} />

      <nav className="mt-8 flex gap-1 border-b border-border">
        {([
          ["watch", "Watch", Play],
          ["script", "Script", FileText],
          ["footage", `Footage (${p.media.length})`, Film],
          ["activity", "Activity", ListChecks],
        ] as const).map(([id, label, Icon]) => (
          <button
            key={id}
            type="button"
            onClick={() => setTab(id)}
            className={`-mb-px flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm ${
              tab === id ? "border-accent text-fg" : "border-transparent text-fg-muted hover:text-fg-2"
            }`}
          >
            <Icon size={14} aria-hidden /> {label}
          </button>
        ))}
      </nav>

      <div className="pt-6">
        {tab === "watch" && <WatchTab p={p} seek={seek} />}
        {tab === "script" && (
          <ScriptTab
            p={p}
            onSeek={(t) => {
              setSeek({ t, n: Date.now() });
              setTab("watch");
            }}
            hasVideo={!!output}
          />
        )}
        {tab === "footage" && <FootageTab p={p} />}
        {tab === "activity" && <ActivityTab p={p} />}
      </div>

      {building && <BuildOverlay state={building} onClose={() => setBuilding(null)} />}
    </div>
  );
}

const STAGE_ICON: Record<string, typeof Circle> = {
  research: FileText, script: FileText, voice: Mic, footage: Film, edit: Layers, render: Clapperboard, publish: ExternalLink,
};

function StageTracker({ stages }: { stages: Stage[] }) {
  if (!stages.length) return null;
  return (
    <ol className="mt-7 grid gap-2" style={{ gridTemplateColumns: `repeat(${stages.length}, minmax(0, 1fr))` }}>
      {stages.map((s) => {
        const Icon = STAGE_ICON[s.id] ?? Circle;
        const took = s.startedAt && s.endedAt ? s.endedAt - s.startedAt : undefined;
        const tone =
          s.status === "done" ? "border-emerald-500/40 bg-emerald-500/5"
          : s.status === "running" ? "border-amber-400/60 bg-amber-400/5"
          : s.status === "failed" ? "border-red-500/60 bg-red-500/5"
          : "border-border bg-bg-1";
        return (
          <li key={s.id} className={`min-w-0 rounded-lg border p-3 ${tone}`} title={s.detail}>
            <div className="flex items-center gap-1.5 text-xs font-medium">
              {s.status === "done" ? <Check size={13} className="text-emerald-600 dark:text-emerald-400" aria-hidden />
                : s.status === "running" ? <Loader2 size={13} className="animate-spin text-amber-700 dark:text-amber-300" aria-hidden />
                : s.status === "failed" ? <AlertTriangle size={13} className="text-red-600 dark:text-red-400" aria-hidden />
                : <Icon size={13} className="text-fg-muted" aria-hidden />}
              <span className={s.status === "pending" ? "text-fg-muted" : ""}>{s.label}</span>
              {took !== undefined && <span className="ml-auto text-[10px] text-fg-muted">{fmtDuration(took)}</span>}
            </div>
            <div className="mt-1 line-clamp-2 text-[11px] leading-snug text-fg-muted">
              {s.detail ?? (s.status === "pending" ? "Waiting" : "")}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function WatchTab({ p, seek }: { p: Production; seek: { t: number; n: number } | null }) {
  const output = p.outputs.find((o) => o.kind === "final") ?? p.outputs[0];
  const ref = useRef<HTMLVideoElement>(null);
  const [time, setTime] = useState(0);
  const current = useMemo(() => beatAt(p, time), [p, time]);

  useEffect(() => {
    const el = ref.current;
    if (!seek || !el) return;
    el.currentTime = seek.t;
    void el.play().catch(() => undefined);
  }, [seek]);

  if (!output) {
    return (
      <div className="rounded-lg border border-dashed border-border p-10 text-center text-sm text-fg-muted">
        No render yet. It appears here when the Render stage finishes.
        {p.narration && (
          <div className="mx-auto mt-6 max-w-md">
            <div className="mb-2 text-xs">Narration so far</div>
            <audio controls src={feedUrl(p.narration.src, p.slug)} className="w-full" />
          </div>
        )}
      </div>
    );
  }
  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_300px]">
      <div>
        <video
          ref={ref}
          src={feedUrl(output.src, p.slug)}
          poster={output.thumb ? feedUrl(output.thumb, p.slug) : undefined}
          controls
          onTimeUpdate={(e) => setTime(e.currentTarget.currentTime)}
          className="aspect-video w-full rounded-lg bg-black"
        />
        <div className="mt-3 min-h-[3rem] rounded-lg bg-bg-1 px-4 py-3 text-sm leading-relaxed text-fg-2">
          {current ? current.text : <span className="text-fg-muted">Play to follow the script</span>}
        </div>
      </div>
      <div className="space-y-3 text-xs">
        <Stat label="Length" value={fmtDuration(output.durationSec)} />
        <Stat label="Render file size" value={fmtBytes(output.sizeBytes)} />
        {output.encoder && <Stat label="Encoder" value={output.encoder} />}
        {output.seconds !== undefined && <Stat label="Render time" value={`${output.seconds.toFixed(0)} s`} />}
        {output.speed_vs_realtime !== undefined && (
          <Stat label="Speed" value={`${output.speed_vs_realtime.toFixed(2)}× real time`} />
        )}
        {output.stages && (
          <div className="rounded-lg bg-bg-1 p-3">
            <div className="mb-2 font-medium text-fg-2">Time per step</div>
            {Object.entries(output.stages).map(([k, v]) => (
              <div key={k} className="flex justify-between py-0.5 text-fg-muted">
                <span className="truncate pr-2">{k}</span>
                <span>{v.toFixed(1)} s</span>
              </div>
            ))}
          </div>
        )}
        <a href={feedUrl("renders/project.mlt", p.slug)} className="block text-fg-muted underline hover:text-fg-2">
          Render project (.mlt)
        </a>
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between rounded-lg bg-bg-1 px-3 py-2">
      <span className="text-fg-muted">{label}</span>
      <span className="font-medium text-fg-2">{value}</span>
    </div>
  );
}

function beatAt(p: Production, t: number): Beat | undefined {
  for (const ch of p.script.chapters) for (const b of ch.beats) if (b.start !== undefined && b.end !== undefined && t >= b.start && t < b.end) return b;
  return undefined;
}

function ScriptTab({ p, onSeek, hasVideo }: { p: Production; onSeek: (t: number) => void; hasVideo: boolean }) {
  const media = new Map(p.media.map((m) => [m.id, m]));
  let prevVisual: MediaAsset | undefined;
  return (
    <div className="space-y-8">
      {p.script.chapters.map((ch, ci) => (
        <section key={ch.id}>
          <h3 className="mb-3 flex items-baseline gap-3 text-sm font-semibold">
            <span className="text-fg-muted">{String(ci + 1).padStart(2, "0")}</span> {ch.title}
          </h3>
          <ol className="space-y-2">
            {ch.beats.map((b, bi) => {
              const m = b.visual.mediaId ? media.get(b.visual.mediaId) : b.visual.hold ? prevVisual : undefined;
              if (m) prevVisual = m;
              return (
                <li key={bi}>
                  <button
                    type="button"
                    disabled={!hasVideo || b.start === undefined}
                    onClick={() => b.start !== undefined && onSeek(b.start)}
                    className="flex w-full gap-4 rounded-lg border border-border bg-bg-1 p-3 text-left hover:border-border-strong disabled:cursor-default"
                  >
                    <div className="w-12 shrink-0 pt-0.5 text-[11px] tabular-nums text-fg-muted">
                      {b.start !== undefined ? fmtDuration(b.start) : "–"}
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="text-sm leading-relaxed">{b.text}</p>
                      <div className="mt-2 flex flex-wrap gap-1.5 text-[10px]">
                        {b.label?.text && (
                          <span className="rounded bg-amber-400/10 px-1.5 py-0.5 text-amber-800 dark:text-amber-200">
                            Label: {b.label.text}
                            {b.label.sub ? ` · ${b.label.sub}` : ""}
                          </span>
                        )}
                        {b.visual.motion && <span className="rounded bg-bg-3 px-1.5 py-0.5 text-fg-2">Motion: {b.visual.motion}</span>}
                        {b.visual.hold && <span className="rounded bg-bg-3 px-1.5 py-0.5 text-fg-2">Holds previous shot</span>}
                        {b.pace && b.pace !== 1 && <span className="rounded bg-bg-3 px-1.5 py-0.5 text-fg-2">Pace {b.pace}×</span>}
                      </div>
                    </div>
                    <div className="h-14 w-24 shrink-0 overflow-hidden rounded bg-bg-3">
                      {m?.thumb && <img src={feedUrl(m.thumb, p.slug)} alt="" className="h-full w-full object-cover" />}
                    </div>
                  </button>
                </li>
              );
            })}
          </ol>
        </section>
      ))}
    </div>
  );
}

function FootageTab({ p }: { p: Production }) {
  const [open, setOpen] = useState<MediaAsset | null>(null);
  return (
    <>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {p.media.map((m) => (
          <button
            key={m.id}
            type="button"
            onClick={() => setOpen(m)}
            className="overflow-hidden rounded-lg border border-border bg-bg-1 text-left hover:border-border-strong"
          >
            <div className="relative aspect-video bg-bg-3">
              {m.thumb && <img src={feedUrl(m.thumb, p.slug)} alt="" className="h-full w-full object-cover" />}
              <span className="absolute left-1.5 top-1.5 flex items-center gap-1 rounded bg-black/70 px-1.5 py-0.5 text-[10px] text-white">
                {m.type === "image" ? <ImageIcon size={10} aria-hidden /> : <Film size={10} aria-hidden />}
                {m.type === "video" ? fmtDuration(m.durationSec) : "photo"}
              </span>
            </div>
            <div className="space-y-1 p-3 text-xs">
              <div className="truncate font-medium">{m.name}</div>
              <div className="truncate text-fg-muted">{m.source ?? "Unknown source"}</div>
              <div className="flex items-center justify-between">
                <span className={`rounded px-1.5 py-0.5 text-[10px] ${m.license ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300" : "bg-red-500/10 text-red-700 dark:text-red-300"}`}>
                  {m.license ?? "No licence recorded"}
                </span>
                <span className="text-[10px] text-fg-muted">used {m.usedIn.length}×</span>
              </div>
            </div>
          </button>
        ))}
      </div>
      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-8" onClick={() => setOpen(null)}>
          <div className="w-full max-w-4xl rounded-xl bg-bg-1 p-4" onClick={(e) => e.stopPropagation()}>
            <div className="mb-3 flex items-center">
              <div className="text-sm font-medium">{open.name}</div>
              <button type="button" onClick={() => setOpen(null)} className="ml-auto text-fg-muted hover:text-fg" aria-label="Close">
                <X size={16} />
              </button>
            </div>
            {open.type === "image" ? (
              <img src={feedUrl(open.src, p.slug)} alt={open.name} className="max-h-[70vh] w-full rounded object-contain" />
            ) : (
              <video src={feedUrl(open.src, p.slug)} controls autoPlay className="aspect-video w-full rounded bg-black" />
            )}
            <div className="mt-3 grid grid-cols-2 gap-2 text-xs text-fg-2">
              <div>Source: {open.source ?? "–"}</div>
              <div>Licence: {open.license ?? "–"}</div>
              <div>Credit: {open.credit ?? "–"}</div>
              <div>
                Original:{" "}
                {open.url ? <a href={open.url} target="_blank" rel="noreferrer" className="underline">link</a> : "–"}
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

function ActivityTab({ p }: { p: Production }) {
  const items = [...p.activity].reverse();
  if (!items.length) return <div className="text-sm text-fg-muted">Nothing logged yet.</div>;
  return (
    <ol className="space-y-1.5">
      {items.map((a, i) => (
        <li key={i} className="flex gap-4 rounded-md bg-bg-1 px-3 py-2 text-xs">
          <span className="w-28 shrink-0 text-fg-muted">{new Date(a.t * 1000).toLocaleTimeString()}</span>
          <span className="w-20 shrink-0 font-medium capitalize text-fg-2">{a.stage}</span>
          <span className="text-fg-2">{a.message}</span>
        </li>
      ))}
    </ol>
  );
}

function BuildOverlay({ state, onClose }: { state: { msg: string; f: number; error?: string }; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="w-[420px] rounded-xl border border-border bg-bg-1 p-6">
        <div className="mb-1 flex items-center gap-2 text-sm font-medium">
          {state.error ? <AlertTriangle size={15} className="text-red-600 dark:text-red-400" /> : <Loader2 size={15} className="animate-spin text-accent" />}
          {state.error ? "Couldn't open in the editor" : "Building the timeline"}
        </div>
        <div className="mb-4 truncate text-xs text-fg-muted">{state.error ?? state.msg}</div>
        <div className="h-1.5 overflow-hidden rounded-full bg-bg-3">
          <div className="h-full rounded-full bg-accent transition-all" style={{ width: `${Math.round(state.f * 100)}%` }} />
        </div>
        {state.error && (
          <button type="button" onClick={onClose} className="mt-4 flex items-center gap-1 text-xs text-fg-2 hover:text-fg">
            Close <ArrowRight size={12} />
          </button>
        )}
      </div>
    </div>
  );
}
