/**
 * Documentary Studio: connects the desktop app to a workspace folder on this computer (a checkout
 * of the Documentary repo with its Python pipeline set up), serves that workspace's Studio feed to
 * the dashboard, and runs pipeline steps ("Make video") as local processes. Progress reaches the
 * dashboard through the feed the pipeline itself publishes, so nothing here needs to parse output.
 */
import { app, dialog, ipcMain, BrowserWindow, shell } from "electron";
import { spawn, type ChildProcess } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const FEED_REL = path.join("editor", "apps", "web", "public", "studio-feed");
const settingsPath = () => path.join(app.getPath("userData"), "studio.json");

let workspace: string | null = null;
const running = new Map<string, ChildProcess>();

function isWorkspace(dir: string): boolean {
  return fs.existsSync(path.join(dir, "pipeline", "produce.py")) && fs.existsSync(path.join(dir, "projects"));
}

export function loadWorkspace(): string | null {
  const candidates: string[] = [];
  try {
    const saved = JSON.parse(fs.readFileSync(settingsPath(), "utf8")) as { workspace?: string };
    if (saved.workspace) candidates.push(saved.workspace);
  } catch {
    /* first run */
  }
  if (process.env.DOCUMENTARY_HOME) candidates.push(process.env.DOCUMENTARY_HOME);
  candidates.push(path.join(os.homedir(), "Documentary"), path.join(os.homedir(), "Documents", "Documentary"));
  workspace = candidates.find(isWorkspace) ?? null;
  return workspace;
}

function saveWorkspace(dir: string): void {
  fs.mkdirSync(path.dirname(settingsPath()), { recursive: true });
  fs.writeFileSync(settingsPath(), JSON.stringify({ workspace: dir }, null, 1));
  workspace = dir;
}

/** Folder the app:// protocol serves at /studio-feed/ (null → the bundled demo feed). */
export function studioFeedRoot(): string | null {
  if (!workspace) return null;
  const feed = path.join(workspace, FEED_REL);
  return fs.existsSync(feed) ? feed : null;
}

function python(ws: string): string {
  const win = path.join(ws, ".venv", "Scripts", "python.exe");
  const nix = path.join(ws, ".venv", "bin", "python");
  if (fs.existsSync(win)) return win;
  if (fs.existsSync(nix)) return nix;
  return process.platform === "win32" ? "python" : "python3";
}

type Action = "produce" | "fetch";

function commandFor(ws: string, action: Action, slug: string): string[] {
  const project = path.join("projects", slug);
  const feed = FEED_REL;
  if (action === "fetch") {
    return ["-m", "footage.fetch", path.join(project, "footage", "picks.json"),
            "--candidates", path.join(project, "footage", "candidates.json"),
            "--out", path.join(project, "footage", "media")];
  }
  const media = path.join(project, "footage", "media");
  return ["-m", "pipeline.produce", project, ...(fs.existsSync(path.join(ws, media)) ? ["--media", media] : []),
          "--out", path.join("out", slug), "--feed", feed];
}

function logPath(slug: string, action: Action): string {
  const dir = path.join(app.getPath("userData"), "logs");
  fs.mkdirSync(dir, { recursive: true });
  return path.join(dir, `${slug}-${action}.log`);
}

export function registerStudioIpc(): void {
  ipcMain.handle("studio:workspace", () => ({ workspace, feed: studioFeedRoot(), running: [...running.keys()] }));

  ipcMain.handle("studio:choose-workspace", async (event) => {
    const win = BrowserWindow.fromWebContents(event.sender) ?? undefined;
    const res = await dialog.showOpenDialog(win!, {
      title: "Choose your Documentary folder",
      properties: ["openDirectory"],
    });
    if (res.canceled || !res.filePaths[0]) return { ok: false, workspace };
    const dir = res.filePaths[0];
    if (!isWorkspace(dir)) {
      return { ok: false, workspace, error: "That folder isn't a Documentary workspace (no pipeline/ and projects/ inside)." };
    }
    saveWorkspace(dir);
    return { ok: true, workspace: dir };
  });

  ipcMain.handle("studio:run", (_event, args: { action: Action; slug: string }) => {
    const ws = workspace;
    if (!ws) return { ok: false, error: "Choose your Documentary folder first." };
    const action: Action = args?.action === "fetch" ? "fetch" : "produce";
    const slug = String(args?.slug ?? "");
    if (!/^[a-z0-9][a-z0-9-]*$/i.test(slug) || !fs.existsSync(path.join(ws, "projects", slug))) {
      return { ok: false, error: `No production "${slug}" in ${ws}\\projects.` };
    }
    const key = `${slug}:${action}`;
    if (running.has(key)) return { ok: false, error: "Already running." };
    const log = fs.createWriteStream(logPath(slug, action));
    const env = { ...process.env, KOKORO_DIR: path.join(ws, "models", "kokoro"), PYTHONUNBUFFERED: "1",
                  STUDIO_FEED: path.join(ws, FEED_REL) };
    const child = spawn(python(ws), commandFor(ws, action, slug), { cwd: ws, env, windowsHide: true });
    child.stdout?.pipe(log);
    child.stderr?.pipe(log);
    running.set(key, child);
    child.on("exit", () => running.delete(key));
    child.on("error", (e) => {
      log.write(`\n[studio] could not start: ${e.message}\n`);
      running.delete(key);
    });
    return { ok: true };
  });

  ipcMain.handle("studio:stop", (_event, args: { slug: string; action: Action }) => {
    const child = running.get(`${args?.slug}:${args?.action ?? "produce"}`);
    child?.kill();
    return { ok: !!child };
  });

  ipcMain.handle("studio:open-output", (_event, args: { slug: string }) => {
    if (!workspace) return { ok: false };
    const file = path.join(workspace, "out", String(args?.slug ?? ""), "final.mp4");
    if (fs.existsSync(file)) shell.showItemInFolder(file);
    else shell.openPath(path.join(workspace, "out"));
    return { ok: true };
  });

  app.on("before-quit", () => {
    for (const child of running.values()) child.kill();
  });
}
