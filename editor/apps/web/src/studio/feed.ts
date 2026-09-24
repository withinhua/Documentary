/**
 * The Studio feed: static JSON + media written by the pipeline (`python -m pipeline.feed`).
 * Served from `public/studio-feed` in dev, or from any static host / R2 bucket via VITE_STUDIO_FEED.
 */
import { useEffect, useRef, useState } from "react";

export const FEED_BASE = (
  (import.meta.env.VITE_STUDIO_FEED as string | undefined) ?? "./studio-feed"
).replace(/\/$/, "");

export type StageStatus = "pending" | "running" | "done" | "failed" | "skipped";

export interface Stage {
  id: string;
  label: string;
  status: StageStatus;
  detail?: string;
  startedAt?: number;
  endedAt?: number;
}

export interface Activity {
  t: number;
  stage: string;
  message: string;
}

export interface BeatVisual {
  type?: "clip" | "photo";
  src?: string;
  mediaId?: string;
  in?: number;
  motion?: "push" | "pull" | "left" | "right";
  hold?: boolean;
}

export interface Beat {
  text: string;
  visual: BeatVisual;
  label?: { text: string; sub?: string } | null;
  pace?: number;
  start?: number;
  end?: number;
}

export interface Chapter {
  id: string;
  title: string;
  beats: Beat[];
}

export interface MediaAsset {
  id: string;
  name: string;
  type: "video" | "image" | "audio";
  src: string;
  thumb?: string;
  durationSec?: number;
  usedIn: string[];
  source?: string;
  license?: string;
  credit?: string;
  url?: string | null;
}

export interface RenderOutput {
  kind: "final" | "render";
  src: string;
  thumb?: string;
  durationSec: number;
  sizeBytes: number;
  encoder?: string;
  seconds?: number;
  stages?: Record<string, number>;
  speed_vs_realtime?: number;
}

export interface Production {
  slug: string;
  title: string;
  topic?: string;
  voice?: { voice?: string; speed?: number };
  createdAt?: number;
  updatedAt: number;
  stages: Stage[];
  activity: Activity[];
  script: { chapters: Chapter[] };
  media: MediaAsset[];
  narration: { src: string; durationSec: number } | null;
  outputs: RenderOutput[];
}

export interface ProductionSummary {
  slug: string;
  title: string;
  updatedAt: number;
  state: "draft" | "running" | "ready" | "done" | "failed";
  stage?: string | null;
  progress: [number, number];
  thumb?: string | null;
  durationSec?: number | null;
}

/** Absolute URL for a path inside the feed (production-relative when a slug is given). */
export function feedUrl(path: string, slug?: string): string {
  if (/^https?:\/\//.test(path)) return path;
  const rel = slug ? `${slug}/${path}` : path;
  return new URL(`${FEED_BASE}/${rel}`, window.location.href).toString();
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${feedUrl(path)}?t=${Date.now()}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
  return (await res.json()) as T;
}

export const fetchIndex = () => getJson<{ productions: ProductionSummary[] }>("index.json");
export const fetchProduction = (slug: string) => getJson<Production>(`${slug}/production.json`);

/** Poll while something is running (fast), otherwise slowly, so progress shows up live. */
export function usePolled<T>(load: (() => Promise<T>) | null, isLive: (v: T) => boolean) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const liveRef = useRef(isLive);
  liveRef.current = isLive;

  useEffect(() => {
    if (!load) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      try {
        const v = await load();
        if (!alive) return;
        setData(v);
        setError(null);
        timer = setTimeout(tick, liveRef.current(v) ? 2000 : 10000);
      } catch (e) {
        if (!alive) return;
        setError(e instanceof Error ? e.message : String(e));
        timer = setTimeout(tick, 5000);
      }
    };
    void tick();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [load]);

  return { data, error };
}

export function fmtDuration(sec?: number | null): string {
  if (!sec && sec !== 0) return "–";
  const s = Math.round(sec);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}` : `${m}:${String(r).padStart(2, "0")}`;
}

export function fmtAgo(epochSec?: number): string {
  if (!epochSec) return "";
  const d = Date.now() / 1000 - epochSec;
  if (d < 60) return "just now";
  if (d < 3600) return `${Math.floor(d / 60)} min ago`;
  if (d < 86400) return `${Math.floor(d / 3600)} h ago`;
  return new Date(epochSec * 1000).toLocaleDateString();
}

export function fmtBytes(n: number): string {
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(0)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}
