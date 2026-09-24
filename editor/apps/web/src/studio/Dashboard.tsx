/**
 * Dashboard: every production at a glance, what's running right now, footage coverage, and one
 * live activity feed across all videos. Polls the feed every 2 s while anything is running.
 */
import { useCallback, useMemo } from "react";
import { AlertTriangle, Check, Circle, Film, Loader2 } from "@/icons/lucide-compat";
import {
  fetchIndex,
  fetchProduction,
  feedUrl,
  fmtAgo,
  fmtDuration,
  usePolled,
  type Activity,
  type Production,
  type ProductionSummary,
  type Stage,
} from "./feed";

const STATE_TONE: Record<ProductionSummary["state"], string> = {
  draft: "bg-bg-3 text-fg-2",
  running: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  ready: "bg-sky-500/15 text-sky-700 dark:text-sky-300",
  done: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  failed: "bg-red-500/15 text-red-700 dark:text-red-300",
};

async function loadAll(): Promise<{ index: ProductionSummary[]; details: Production[] }> {
  const { productions } = await fetchIndex();
  const details = await Promise.all(productions.map((p) => fetchProduction(p.slug).catch(() => null)));
  return { index: productions, details: details.filter((d): d is Production => !!d) };
}

export function Dashboard({ onOpen }: { onOpen: (slug: string) => void }) {
  const load = useCallback(loadAll, []);
  const { data, error } = usePolled(load, (d) => d.index.some((p) => p.state === "running"));
  const index = data?.index ?? [];
  const details = useMemo(() => new Map((data?.details ?? []).map((d) => [d.slug, d])), [data]);

  const running = (data?.details ?? []).flatMap((d) =>
    d.stages.filter((s) => s.status === "running").map((s) => ({ prod: d, stage: s })),
  );
  const activity = useMemo(() => {
    const all: Array<Activity & { title: string; slug: string }> = [];
    for (const d of data?.details ?? []) for (const a of d.activity) all.push({ ...a, title: d.title, slug: d.slug });
    return all.sort((a, b) => b.t - a.t).slice(0, 60);
  }, [data]);
  const cov = index.reduce(
    (acc, p) => {
      if (p.coverage) {
        acc.needed += p.coverage.needed;
        acc.downloaded += p.coverage.downloaded;
      }
      return acc;
    },
    { needed: 0, downloaded: 0 },
  );

  if (!data) {
    return <div className="p-10 text-sm text-fg-muted">{error ? `Couldn't load the feed: ${error}` : "Loading…"}</div>;
  }
  return (
    <div className="mx-auto grid max-w-7xl gap-8 px-8 pb-16 pt-7">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Tile label="Videos" value={String(index.length)} />
        <Tile label="Running now" value={String(running.length)} live={running.length > 0} />
        <Tile label="Ready to watch" value={String(index.filter((p) => p.state === "ready" || p.state === "done").length)} />
        <Tile label="Beats with real footage" value={cov.needed ? `${cov.downloaded}/${cov.needed}` : "–"} />
      </div>

      <section className="grid gap-3">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-fg-muted">Now running</h2>
        {running.length ? (
          <div className="grid gap-2">
            {running.map(({ prod, stage }) => (
              <button
                key={prod.slug + stage.id}
                type="button"
                onClick={() => onOpen(prod.slug)}
                className="flex items-center gap-3 rounded-lg border border-amber-400/50 bg-amber-400/5 px-4 py-3 text-left hover:bg-amber-400/10"
              >
                <Loader2 size={15} className="shrink-0 animate-spin text-amber-600 dark:text-amber-300" aria-hidden />
                <span className="w-56 shrink-0 truncate text-sm font-medium">{prod.title}</span>
                <span className="w-24 shrink-0 text-xs font-semibold uppercase tracking-wide text-amber-700 dark:text-amber-300">{stage.label}</span>
                <span className="min-w-0 flex-1 truncate text-sm text-fg-2">{stage.detail ?? "Working…"}</span>
                {stage.startedAt && <span className="shrink-0 text-xs tabular-nums text-fg-muted">{fmtDuration(Date.now() / 1000 - stage.startedAt)}</span>}
              </button>
            ))}
          </div>
        ) : (
          <div className="rounded-lg border border-dashed border-border px-4 py-3 text-sm text-fg-muted">Nothing running. Start a production and it shows up here within seconds.</div>
        )}
      </section>

      <div className="grid gap-8 xl:grid-cols-[minmax(0,1fr)_380px]">
        <section className="grid content-start gap-3">
          <h2 className="text-sm font-semibold uppercase tracking-wider text-fg-muted">All videos</h2>
          <div className="grid gap-3 lg:grid-cols-2">
            {index.map((p) => (
              <VideoCard key={p.slug} p={p} d={details.get(p.slug)} onOpen={() => onOpen(p.slug)} />
            ))}
          </div>
        </section>
        <section className="grid content-start gap-3">
          <h2 className="text-sm font-semibold uppercase tracking-wider text-fg-muted">Live activity</h2>
          <ol className="grid max-h-[70vh] gap-1 overflow-y-auto rounded-lg border border-border bg-bg-1 p-2">
            {activity.map((a, i) => (
              <li key={i} className="grid grid-cols-[64px_minmax(0,1fr)] gap-2 rounded px-2 py-1.5 text-xs hover:bg-hover">
                <span className="tabular-nums text-fg-muted">{new Date(a.t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</span>
                <span className="min-w-0">
                  <span className="font-medium capitalize text-fg-2">{a.stage}</span>
                  <span className="text-fg-muted"> · {a.title}</span>
                  <span className="block truncate text-fg-2" title={a.message}>{a.message}</span>
                </span>
              </li>
            ))}
            {!activity.length && <li className="px-2 py-4 text-xs text-fg-muted">No activity yet.</li>}
          </ol>
        </section>
      </div>
    </div>
  );
}

function Tile({ label, value, live }: { label: string; value: string; live?: boolean }) {
  return (
    <div className="rounded-lg border border-border bg-bg-1 px-4 py-3">
      <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-fg-muted">
        {live && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-amber-500" />}
        {label}
      </div>
      <div className="mt-1 text-2xl font-semibold tabular-nums">{value}</div>
    </div>
  );
}

function StageDots({ stages }: { stages: Stage[] }) {
  return (
    <ol className="flex gap-1">
      {stages.map((s) => (
        <li key={s.id} title={`${s.label}: ${s.status}${s.detail ? ` — ${s.detail}` : ""}`} className="flex min-w-0 flex-1 flex-col gap-1">
          <span
            className={`h-1.5 rounded-full ${
              s.status === "done" ? "bg-emerald-500"
              : s.status === "running" ? "animate-pulse bg-amber-500"
              : s.status === "failed" ? "bg-red-500"
              : "bg-bg-3"
            }`}
          />
          <span className="truncate text-[10px] text-fg-muted">{s.label}</span>
        </li>
      ))}
    </ol>
  );
}

function VideoCard({ p, d, onOpen }: { p: ProductionSummary; d?: Production; onOpen: () => void }) {
  const c = p.coverage;
  const pct = (n: number) => (c && c.needed ? `${(n / c.needed) * 100}%` : "0%");
  return (
    <button type="button" onClick={onOpen} className="grid gap-3 rounded-lg border border-border bg-bg-1 p-3 text-left hover:border-border-strong">
      <div className="flex gap-3">
        <div className="relative h-20 w-36 shrink-0 overflow-hidden rounded bg-bg-3">
          {p.thumb ? <img src={feedUrl(p.thumb)} alt="" className="h-full w-full object-cover" /> : <Film size={20} className="m-auto mt-7 text-fg-muted" aria-hidden />}
          {p.durationSec ? <span className="absolute bottom-1 right-1 rounded bg-black/70 px-1 text-[10px] text-white">{fmtDuration(p.durationSec)}</span> : null}
        </div>
        <div className="min-w-0 flex-1">
          <div className="line-clamp-2 text-sm font-medium">{p.title}</div>
          <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] text-fg-muted">
            <span className={`rounded px-1.5 py-0.5 font-medium capitalize ${STATE_TONE[p.state]}`}>
              {p.state === "running" && p.stage ? p.stage : p.state}
            </span>
            {p.chapters ? <span>{p.chapters} chapters · {p.beats} beats</span> : null}
            <span>· {fmtAgo(p.updatedAt)}</span>
          </div>
          {p.detail && <div className="mt-1 truncate text-xs text-fg-2">{p.detail}</div>}
        </div>
      </div>
      {d && <StageDots stages={d.stages} />}
      {c && (
        <div className="grid gap-1">
          <div className="flex h-2 overflow-hidden rounded-full bg-bg-3" title="Footage: downloaded / approved / needed">
            <span className="bg-emerald-500" style={{ width: pct(c.downloaded) }} />
            <span className="bg-emerald-500/40" style={{ width: pct(Math.max(0, c.approved - c.downloaded)) }} />
          </div>
          <div className="flex flex-wrap gap-x-3 text-[11px] text-fg-muted">
            <span className="flex items-center gap-1"><Check size={11} className="text-emerald-600" aria-hidden />{c.downloaded} footage in</span>
            <span>{c.approved} approved</span>
            <span>{c.needed - c.approved} use cards</span>
            {c.fair_use ? <span className="flex items-center gap-1"><AlertTriangle size={11} aria-hidden />{c.fair_use} fair use</span> : null}
            <span className="flex items-center gap-1"><Circle size={9} aria-hidden />{c.needed} need a picture</span>
          </div>
        </div>
      )}
    </button>
  );
}
