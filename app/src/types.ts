// Shared between the Electron main/preload (electron/) and the renderer (src/).
// Shapes follow docs/ENGINE_SPEC.md (CLI events and groups_json).

export type Role = "keeper" | "duplicate" | "ignored";
export type GroupStatus = "pending" | "confirmed" | "applied" | "dismissed";

export interface Member {
  file_id: number;
  path: string;
  role: Role;
  size: number;
  duration: number | null;
  width: number | null;
  height: number | null;
  bit_rate: number | null;
  codec: string | null;
  score: number | null;
  thumb: string | null;
  status: string;
}

export interface Group {
  id: number;
  scan_id: number;
  status: GroupStatus;
  members: Member[];
}

export interface BatchInfo {
  batch_id: string;
  at: string;
  moved: number;
  restored: number;
  failed: number;
}

export interface ScanStats {
  files: number;
  cached: number;
  unreadable: number;
  groups: number;
  duplicates: number;
}

export type EngineEvent =
  | { type: "scan_started"; scan_id: number; roots: string[] }
  | { type: "files_found"; count: number }
  | { type: "progress"; phase: string; done: number; total: number; path: string }
  | { type: "file_error"; path: string; error: string }
  | { type: "group"; group_id: number; file_ids: number[] }
  | ({ type: "scan_done"; scan_id: number } & ScanStats)
  | { type: "groups"; scan_id: number | null; groups: Group[] }
  | { type: "ok"; confirmed?: number }
  | { type: "skip"; [key: string]: unknown }
  | { type: "apply_done"; batch_id: string; moved: number; failed: number }
  | { type: "undo_done"; batch_id: string; restored: number; failed: number }
  | { type: "history"; batches: BatchInfo[] }
  | { type: "error"; message: string }
  // Added by the app's main process, not by the engine:
  | { type: "log"; line: string }
  | { type: "stderr"; text: string };

export interface Settings {
  roots: string[];
  dupRoot: string;
}

export interface RunResult {
  code: number;
  events: EngineEvent[];
}

export interface Api {
  pickFolder(): Promise<string | null>;
  pickFolders(): Promise<string[]>;
  getSettings(): Promise<Settings>;
  saveSettings(s: Settings): Promise<void>;
  run(args: string[]): Promise<RunResult>;
  cancel(): Promise<boolean>;
  onEvent(cb: (ev: EngineEvent) => void): () => void;
  openPath(p: string): Promise<string>;
  showInFolder(p: string): Promise<void>;
  fileUrl(p: string): string;
  pathForFile(file: File): string;
}
