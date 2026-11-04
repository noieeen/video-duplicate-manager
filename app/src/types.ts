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
  trashed: number;
  bytes: number;
}

export interface ScanStats {
  files: number;
  cached: number;
  unreadable: number;
  groups: number;
  duplicates: number;
}

export interface ConvertResult {
  input: string;
  output: string;
  mode: string;
  encoder: string;
  /** Speed used for a re-encode ("quality" | "fast"); null in copy mode. */
  speed: string | null;
  seconds: number;
  size_in: number;
  size_out: number;
}

/** One slice of the Space tab donut (engine `library` event). */
export interface LibraryGroup {
  key: string;
  label: string;
  files: number;
  bytes: number;
}

/** One file row in the Space tab file list (engine `library_files` event). */
export interface LibraryFile {
  file_id: number;
  path: string;
  size: number;
  width: number | null;
  height: number | null;
  codec: string | null;
  bit_rate: number | null;
  duration: number | null;
  status: string;
}

export type EngineEvent =
  | { type: "scan_started"; scan_id: number; roots: string[] }
  | { type: "files_found"; count: number }
  | { type: "progress"; phase: string; done: number; total: number; path: string; percent?: number }
  | { type: "file_error"; path: string; error: string }
  | { type: "group"; group_id: number; file_ids: number[] }
  | ({ type: "scan_done"; scan_id: number } & ScanStats)
  | { type: "groups"; scan_id: number | null; groups: Group[] }
  | { type: "ok"; confirmed?: number }
  | { type: "skip"; [key: string]: unknown }
  | { type: "apply_done"; batch_id: string; moved: number; failed: number; bytes: number; mode: "move" | "trash" }
  | { type: "undo_done"; batch_id: string; restored: number; failed: number }
  | { type: "history"; batches: BatchInfo[] }
  | { type: "files"; paths: string[] }
  | { type: "cache_cleared"; fingerprints: number; files_reset: number; thumbs_deleted: number }
  | { type: "convert_started"; total: number; mode: string }
  | ({ type: "converted" } & ConvertResult)
  | { type: "convert_done"; converted: number; failed: number; skipped: number; outputs: string[] }
  | { type: "library"; group_by: string; total_files: number; total_bytes: number; groups: LibraryGroup[] }
  | { type: "library_files"; group_by: string; key: string; total: number; files: LibraryFile[] }
  | { type: "error"; message: string }
  // Added by the app's main process, not by the engine:
  | { type: "log"; line: string }
  | { type: "stderr"; text: string };

export interface Settings {
  roots: string[];
  dupRoot: string;
  /** Scan threads per drive (engine `--workers`). 4 suits a SATA HDD; SSDs can take 6-8. */
  workers: number;
  /** Glob patterns (file or folder name, or full path; case-insensitive) that scans skip. Sent as `scan --exclude`. */
  excludes: string[];
}

export interface RunResult {
  code: number;
  events: EngineEvent[];
}

export interface Api {
  pickFolder(): Promise<string | null>;
  pickFolders(): Promise<string[]>;
  pickFiles(): Promise<string[]>;
  trash(path: string): Promise<void>;
  getSettings(): Promise<Settings>;
  saveSettings(s: Settings): Promise<void>;
  run(args: string[]): Promise<RunResult>;
  cancel(): Promise<boolean>;
  onEvent(cb: (ev: EngineEvent) => void): () => void;
  openPath(p: string): Promise<string>;
  showInFolder(p: string): Promise<void>;
  fileUrl(p: string): string;
  /** media://local/<encoded path>: a video file URL for <video>, served with Range support. */
  mediaUrl(p: string): string;
  pathForFile(file: File): string;
}
