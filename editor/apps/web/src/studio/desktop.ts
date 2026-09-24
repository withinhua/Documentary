/** Bridge to the Documentary Studio desktop app (undefined in a browser). */
export interface StudioDesktop {
  workspace(): Promise<{ workspace: string | null; feed: string | null; running: string[] }>;
  chooseWorkspace(): Promise<{ ok: boolean; workspace: string | null; error?: string }>;
  run(slug: string, action?: "produce" | "fetch"): Promise<{ ok: boolean; error?: string }>;
  stop(slug: string, action?: "produce" | "fetch"): Promise<{ ok: boolean }>;
  openOutput(slug: string): Promise<{ ok: boolean }>;
}

export const desktop: StudioDesktop | undefined =
  typeof window !== "undefined" ? (window as unknown as { documentaryStudio?: StudioDesktop }).documentaryStudio : undefined;
