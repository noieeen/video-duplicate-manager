import { app, BrowserWindow, dialog, ipcMain, net, protocol, shell } from "electron";
import { execFile, spawn, type ChildProcess } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";
import * as readline from "node:readline";
import { pathToFileURL } from "node:url";
import type { EngineEvent, RunResult, Settings } from "../src/types";

// Must be called before the app is ready. Lets the renderer load thumbnails via thumb://local/<encoded path>.
protocol.registerSchemesAsPrivileged([
  { scheme: "thumb", privileges: { standard: true, secure: true, supportFetchAPI: true } },
]);

// Dev only. The built main file is <repo>/app/dist-electron/main.js, so ../.. is the repo root.
const REPO_ROOT = path.resolve(__dirname, "../..");

let mainWindow: BrowserWindow | null = null;
let engineProc: ChildProcess | null = null;

const userData = (): string => app.getPath("userData");
const dbPath = (): string => path.join(userData(), "library.db");
const settingsPath = (): string => path.join(userData(), "settings.json");
const thumbsDir = (): string => path.join(userData(), "thumbs");

interface Launch {
  cmd: string;
  prefix: string[];
  cwd: string;
  env: NodeJS.ProcessEnv;
}

type LaunchResult = { ok: true; launch: Launch } | { ok: false; error: string };

function resolveLaunch(): LaunchResult {
  const exe = process.platform === "win32" ? ".exe" : "";
  const env: NodeJS.ProcessEnv = { ...process.env, PYTHONUTF8: "1", PYTHONIOENCODING: "utf-8" };

  let cmd: string;
  let prefix: string[];
  let cwd: string;
  if (app.isPackaged) {
    cmd = path.join(process.resourcesPath, "engine", `engine${exe}`);
    prefix = [];
    cwd = path.dirname(cmd);
  } else {
    cmd =
      process.platform === "win32"
        ? path.join(REPO_ROOT, ".venv", "Scripts", "python.exe")
        : path.join(REPO_ROOT, ".venv", "bin", "python");
    prefix = ["-m", "engine"];
    cwd = REPO_ROOT;
  }
  if (!fs.existsSync(cmd)) return { ok: false, error: `Engine not found: ${cmd}` };

  const ffmpeg = path.join(process.resourcesPath, "ffmpeg", `ffmpeg${exe}`);
  const ffprobe = path.join(process.resourcesPath, "ffmpeg", `ffprobe${exe}`);
  if (fs.existsSync(ffmpeg)) env.FFMPEG_BIN = ffmpeg;
  if (fs.existsSync(ffprobe)) env.FFPROBE_BIN = ffprobe;

  return { ok: true, launch: { cmd, prefix, cwd, env } };
}

function parseLine(line: string): EngineEvent {
  try {
    const obj: unknown = JSON.parse(line);
    if (obj && typeof obj === "object" && typeof (obj as { type?: unknown }).type === "string") {
      return obj as EngineEvent;
    }
  } catch {
    // Not JSON: fall through and forward as a log line.
  }
  return { type: "log", line };
}

function sendEvent(ev: EngineEvent): void {
  if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send("engine:event", ev);
}

// Runs one engine command. Only one engine process at a time.
function runEngine(args: string[]): Promise<RunResult> {
  if (engineProc) {
    return Promise.resolve({ code: -1, events: [{ type: "error", message: "Another engine command is already running." }] });
  }
  const resolved = resolveLaunch();
  if (!resolved.ok) {
    return Promise.resolve({ code: -1, events: [{ type: "error", message: resolved.error }] });
  }
  const { cmd, prefix, cwd, env } = resolved.launch;

  const events: EngineEvent[] = [];
  const emit = (ev: EngineEvent): void => {
    events.push(ev);
    sendEvent(ev);
  };

  // --db is a global option, so it goes before the subcommand.
  const child = spawn(cmd, [...prefix, "--db", dbPath(), ...args], { cwd, env, windowsHide: true });
  engineProc = child;

  const stderrChunks: string[] = [];
  child.stderr.setEncoding("utf8");
  child.stderr.on("data", (chunk: string) => stderrChunks.push(chunk));

  child.stdout.setEncoding("utf8");
  const rl = readline.createInterface({ input: child.stdout });
  rl.on("line", (raw) => {
    const line = raw.trim();
    if (line) emit(parseLine(line));
  });

  return new Promise<RunResult>((resolve) => {
    child.on("error", (err) => {
      // A spawn failure may not be followed by "close", so resolve here as well.
      engineProc = null;
      emit({ type: "error", message: `Failed to start engine: ${err.message}` });
      resolve({ code: -1, events });
    });
    child.on("close", (code) => {
      engineProc = null;
      const stderr = stderrChunks.join("").trim();
      if (stderr) emit({ type: "stderr", text: stderr });
      resolve({ code: code ?? -1, events });
    });
  });
}

function cancelEngine(): boolean {
  const child = engineProc;
  if (!child || child.pid === undefined) return false;
  if (process.platform === "win32") {
    execFile("taskkill", ["/pid", String(child.pid), "/T", "/F"], () => undefined);
  } else {
    child.kill("SIGKILL");
  }
  return true;
}

function loadSettings(): Settings {
  try {
    const raw = JSON.parse(fs.readFileSync(settingsPath(), "utf8")) as Partial<Settings>;
    return {
      roots: Array.isArray(raw.roots) ? raw.roots.filter((r): r is string => typeof r === "string") : [],
      dupRoot: typeof raw.dupRoot === "string" ? raw.dupRoot : "",
    };
  } catch {
    return { roots: [], dupRoot: "" };
  }
}

function saveSettings(s: Settings): void {
  fs.mkdirSync(userData(), { recursive: true });
  const clean: Settings = { roots: s.roots, dupRoot: s.dupRoot };
  fs.writeFileSync(settingsPath(), JSON.stringify(clean, null, 2), "utf8");
}

function createWindow(): void {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 960,
    minHeight: 640,
    title: "Video Duplicate Manager",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });
  mainWindow.on("closed", () => {
    mainWindow = null;
  });

  const devUrl = process.env.VITE_DEV_SERVER_URL;
  if (devUrl) {
    void mainWindow.loadURL(devUrl);
  } else {
    void mainWindow.loadFile(path.join(__dirname, "../dist/index.html"));
  }
}

function registerIpc(): void {
  ipcMain.handle("dialog:pickFolder", async (): Promise<string | null> => {
    const opts = { properties: ["openDirectory" as const] };
    const res = mainWindow ? await dialog.showOpenDialog(mainWindow, opts) : await dialog.showOpenDialog(opts);
    return res.canceled ? null : (res.filePaths[0] ?? null);
  });
  ipcMain.handle("settings:get", (): Settings => loadSettings());
  ipcMain.handle("settings:save", (_e, s: Settings): void => saveSettings(s));
  ipcMain.handle("engine:run", (_e, args: unknown): Promise<RunResult> => {
    if (!Array.isArray(args) || !args.every((a) => typeof a === "string")) {
      return Promise.resolve({ code: -1, events: [{ type: "error", message: "Invalid engine arguments." }] });
    }
    return runEngine(args as string[]);
  });
  ipcMain.handle("engine:cancel", (): boolean => cancelEngine());
  ipcMain.handle("shell:openPath", (_e, p: string): Promise<string> => shell.openPath(p));
  ipcMain.handle("shell:showInFolder", (_e, p: string): void => shell.showItemInFolder(p));
}

app.whenReady().then(() => {
  // thumb://local/<encodeURIComponent(absolutePath)>. Only files inside the thumbs folder are served.
  protocol.handle("thumb", (request) => {
    const encoded = new URL(request.url).pathname.replace(/^\//, "");
    const file = path.resolve(decodeURIComponent(encoded));
    const rel = path.relative(thumbsDir(), file);
    if (rel.startsWith("..") || path.isAbsolute(rel)) {
      return new Response("forbidden", { status: 403 });
    }
    return net.fetch(pathToFileURL(file).href);
  });

  registerIpc();
  createWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", () => {
  cancelEngine();
});
