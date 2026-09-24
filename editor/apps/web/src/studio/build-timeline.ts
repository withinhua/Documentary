/**
 * "Open in editor": turn a production (script beats + footage + narration) into a real, editable
 * timeline. It uses the same agent tools Claude uses to drive the editor, so anything built here
 * can be changed by hand afterwards or by an agent.
 *
 * Tracks:  Footage (video + photos, house look, Ken Burns)  ·  Labels (text)  ·  Narration (audio)
 * Markers: one per chapter.
 */
import { executeTool } from "@openreel/agent";
import { useProjectStore } from "../stores/project-store";
import { getLiveEditorHost } from "../services/agent/host-singleton";
import { feedUrl, type Beat, type Production } from "./feed";

export type Progress = (message: string, fraction: number) => void;

if (import.meta.env.DEV) {
  // Dev-only handle for inspecting the live project from the console / automated checks.
  (window as unknown as Record<string, unknown>).__studioProject = () => useProjectStore.getState().project;
}

/** The house look, as editor effects, applied to every footage clip. */
const HOUSE_EFFECTS: Array<[string, Record<string, number>]> = [
  ["saturation", { value: -18 }],
  ["contrast", { value: 10 }],
  ["vignette", { amount: 45, size: 55, feather: 70 }],
  ["grain", { amount: 12 }],
];

const MOTION: Record<string, [number, number]> = {
  push: [1.0, 1.12],
  pull: [1.12, 1.0],
  left: [1.06, 1.1],
  right: [1.1, 1.06],
};

const LABEL_STYLE = {
  fontFamily: "Georgia",
  fontSize: 46,
  fontWeight: 600,
  color: "#f5e6c8",
  textAlign: "left",
};

interface Slot {
  beat: Beat;
  start: number;
  end: number;
}

/** Beats with timings, `hold` beats merged into the picture before them. */
function slots(production: Production): Slot[] {
  const out: Slot[] = [];
  let t = 0;
  for (const ch of production.script.chapters) {
    for (const beat of ch.beats) {
      const start = beat.start ?? t;
      const end = beat.end ?? start + Math.max(2, beat.text.split(/\s+/).length / 2.5);
      t = end;
      const prev = out[out.length - 1];
      if (prev && (!beat.visual?.src || beat.visual.hold)) {
        prev.end = end;
        continue;
      }
      out.push({ beat, start, end });
    }
  }
  return out;
}

async function importFromFeed(src: string, name: string, slug: string): Promise<string> {
  const res = await fetch(feedUrl(src, slug));
  if (!res.ok) throw new Error(`Couldn't download ${name} (HTTP ${res.status})`);
  const blob = await res.blob();
  const file = new File([blob], name, { type: blob.type || undefined });
  const result = await useProjectStore.getState().importMedia(file);
  if (!result.success || !result.actionId) {
    throw new Error(result.error?.message ?? `Import failed for ${name}`);
  }
  return result.actionId;
}

function findClip(trackId: string, mediaId: string, start: number): string | undefined {
  const track = useProjectStore.getState().project.timeline.tracks.find((t) => t.id === trackId);
  return track?.clips.find((c) => c.mediaId === mediaId && Math.abs(c.startTime - start) < 1e-3)?.id;
}

async function tool(name: string, args: Record<string, unknown>, warnings: string[]) {
  const r = await executeTool(name, args, getLiveEditorHost());
  if (!r.ok) warnings.push(`${name}: ${r.summary}`);
  return r;
}

export async function buildTimeline(production: Production, progress: Progress): Promise<string[]> {
  const warnings: string[] = [];
  const store = useProjectStore.getState;
  const plan = slots(production);
  const used = production.media.filter((m) => m.usedIn.length > 0);
  const steps = used.length + plan.length + 3;
  let step = 0;
  const tick = (msg: string) => progress(msg, Math.min(1, ++step / steps));

  await tool("create_project", { name: production.title, width: 1920, height: 1080, frameRate: 30 }, warnings);
  tick("Created project");

  // 1. media: proxies from the feed into the project's library
  const mediaIds = new Map<string, string>();
  const sourceLength = new Map(used.map((m) => [m.id, m.durationSec ?? Infinity]));
  for (const m of used) {
    mediaIds.set(m.id, await importFromFeed(m.src, m.name, production.slug));
    tick(`Imported ${m.name}`);
  }
  let narrationId: string | undefined;
  if (production.narration) {
    narrationId = await importFromFeed(production.narration.src, production.narration.src.split("/").pop() ?? "narration.mp3", production.slug);
  }
  tick("Imported narration");

  // 2. tracks (fixed ids so the rest of the build can address them)
  const footage = "studio-footage";
  const voice = "studio-narration";
  await store().addTrack("video", 0, { name: "Footage", trackId: footage });
  await store().addTrack("audio", undefined, { name: "Narration", role: "dialogue", trackId: voice });

  // 3. footage beat by beat
  for (const { beat, start, end } of plan) {
    const v = beat.visual ?? {};
    const mediaId = v.mediaId ? mediaIds.get(v.mediaId) : undefined;
    const dur = end - start;
    if (mediaId) {
      const add = await store().addClip(footage, mediaId, start);
      const clipId = add.success ? findClip(footage, mediaId, start) : undefined;
      if (!clipId) {
        warnings.push(`Couldn't place ${v.src} at ${start.toFixed(1)}s`);
      } else {
        // A shot must fit inside its source: pull the in-point back rather than run past the end.
        const length = (v.mediaId && sourceLength.get(v.mediaId)) || Infinity;
        const inPoint = v.type === "photo" ? 0 : Math.max(0, Math.min(Number(v.in ?? 0), length - dur));
        if (v.type !== "photo" && length < dur) warnings.push(`${v.src} is shorter (${length}s) than its beat (${dur.toFixed(1)}s)`);
        await store().trimClip(clipId, inPoint, inPoint + dur);
        for (const [effectType, params] of HOUSE_EFFECTS) {
          await tool("add_video_effect", { clipId, effectType, params }, warnings);
        }
        const motion = v.motion ?? (v.type === "photo" ? "push" : undefined);
        if (motion && MOTION[motion]) {
          const [a, b] = MOTION[motion];
          const kf = (p: string, time: number, value: number) => ({
            id: `kf-${clipId}-${p}-${time}`, property: p, time, value, easing: "linear",
          });
          await tool("set_clip_keyframes", {
            clipId,
            keyframes: [kf("scale.x", 0, a), kf("scale.y", 0, a), kf("scale.x", dur, b), kf("scale.y", dur, b)],
          }, warnings);
        }
      }
    }
    if (beat.label?.text) {
      const text = beat.label.sub ? `${beat.label.text}\n${beat.label.sub}` : beat.label.text;
      await tool("create_text_clip", {
        clip: { text, startTime: start, duration: Math.min(4, dur), style: LABEL_STYLE, animation: "fade" },
      }, warnings);
    }
    tick(`Placed ${beat.text.slice(0, 40)}…`);
  }

  // Name the track the label overlays landed on.
  for (const track of store().project.timeline.tracks) {
    if (track.id !== footage && track.id !== voice && track.name !== "Labels") {
      await tool("rename_track", { trackId: track.id, name: "Labels" }, warnings);
    }
  }

  // 4. narration + chapter markers
  if (narrationId) await store().addClip(voice, narrationId, 0);
  let t = 0;
  for (const ch of production.script.chapters) {
    const first = ch.beats[0];
    await tool("add_marker", { time: first?.start ?? t, label: ch.title, color: "#d4a24c" }, warnings);
    t = ch.beats[ch.beats.length - 1]?.end ?? t;
  }
  tick("Timeline ready");
  return warnings;
}
