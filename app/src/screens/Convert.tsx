import { useEffect, useRef, useState, type DragEvent } from "react";
import { engineError, findEvent, runCli } from "../engine";
import { fmtMB, fmtPercent } from "../format";
import { samePath } from "../paths";

interface Props {
  report: (msg: string) => void;
}

type Mode = "copy" | "reencode";
type Encoder = "auto" | "nvenc" | "x264";
type OutMode = "source" | "folder";

// Drops are filtered to these; the file picker has the same list in electron/main.ts.
const VIDEO_EXTS = ["ts", "mts", "m2ts", "mkv", "avi", "mov", "flv", "webm", "wmv", "m4v", "mp4"];

interface Row {
  input: string;
  status: "converted" | "error" | "skipped";
  output?: string;
  sizeIn?: number;
  sizeOut?: number;
  encoder?: string;
  // Only for converted rows when "move original to Recycle Bin" was ticked. Error text lives in message.
  trash?: "pending" | "recycled" | "failed";
  message?: string;
}

function baseName(p: string): string {
  return p.split(/[\\/]/).pop() ?? p;
}

function extOf(p: string): string {
  return /\.([^.\\/]+)$/.exec(p)?.[1]?.toLowerCase() ?? "";
}

export default function Convert({ report }: Props) {
  const [files, setFiles] = useState<string[]>([]);
  const [mode, setMode] = useState<Mode>("copy");
  const [encoder, setEncoder] = useState<Encoder>("auto");
  const [outMode, setOutMode] = useState<OutMode>("source");
  const [outDir, setOutDir] = useState("");
  const [trashAfter, setTrashAfter] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const [running, setRunning] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [overall, setOverall] = useState<{ done: number; total: number } | null>(null);
  const [current, setCurrent] = useState<{ path: string; percent: number } | null>(null);
  const [rows, setRows] = useState<Row[]>([]);
  const [summary, setSummary] = useState<string | null>(null);

  // Live event handler is registered once, so run-scoped values live in refs.
  const runActive = useRef(false);
  const trashForRun = useRef(false);
  const cancelRequested = useRef(false);
  const trashChain = useRef<Promise<void>>(Promise.resolve());

  useEffect(
    () =>
      window.api.onEvent((ev) => {
        // file_error and skip are shared with the scan, so only handle them during a convert run.
        if (!runActive.current) return;

        if (ev.type === "convert_started") {
          setOverall({ done: 0, total: ev.total });
        } else if (ev.type === "progress" && ev.phase === "convert") {
          setOverall({ done: ev.done, total: ev.total });
          setCurrent({ path: ev.path, percent: ev.percent ?? 0 });
        } else if (ev.type === "converted") {
          const input = ev.input;
          const trash = trashForRun.current;
          setRows((prev) => [
            ...prev,
            {
              input,
              status: "converted",
              output: ev.output,
              sizeIn: ev.size_in,
              sizeOut: ev.size_out,
              encoder: ev.encoder,
              trash: trash ? "pending" : undefined,
            },
          ]);
          if (trash) {
            // Serialized, so a slow Recycle Bin call never overlaps another one. Never rejects.
            trashChain.current = trashChain.current.then(async () => {
              try {
                await window.api.trash(input);
                setRows((prev) => prev.map((r) => (r.input === input ? { ...r, trash: "recycled" } : r)));
              } catch (e) {
                setRows((prev) =>
                  prev.map((r) => (r.input === input ? { ...r, trash: "failed", message: String(e) } : r)),
                );
              }
            });
          }
        } else if (ev.type === "file_error") {
          setRows((prev) => [...prev, { input: ev.path, status: "error", message: ev.error }]);
        } else if (ev.type === "skip") {
          setRows((prev) => [
            ...prev,
            { input: String(ev.path ?? ""), status: "skipped", message: String(ev.reason ?? "") },
          ]);
        }
      }),
    [],
  );

  function addFiles(paths: string[]) {
    setFiles((prev) => {
      const next = [...prev];
      for (const p of paths) {
        if (!next.some((q) => samePath(q, p))) next.push(p);
      }
      return next.length === prev.length ? prev : next;
    });
  }

  async function addViaDialog() {
    const paths = await window.api.pickFiles();
    if (paths.length > 0) addFiles(paths);
  }

  async function addFromScan() {
    setNotice(null);
    const res = await runCli(["files", "--ext", ".ts"]);
    const err = engineError(res);
    if (err) {
      report(err);
      return;
    }
    const paths = findEvent(res, "files")?.paths ?? [];
    addFiles(paths);
    setNotice(
      paths.length === 0
        ? "No .ts files in the last scan."
        : `Found ${paths.length} .ts file(s) in the last scan. Files already listed are not added twice.`,
    );
  }

  async function chooseOutDir() {
    const p = await window.api.pickFolder();
    if (p) {
      setOutDir(p);
      setOutMode("folder");
    }
  }

  function onDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragOver(false);
    const paths: string[] = [];
    let ignored = 0;
    for (const item of Array.from(e.dataTransfer.items)) {
      if (item.kind !== "file") continue;
      const entry = item.webkitGetAsEntry?.();
      if (entry && entry.isDirectory) {
        ignored++;
        continue;
      }
      const file = item.getAsFile();
      if (!file) continue;
      const p = window.api.pathForFile(file);
      if (VIDEO_EXTS.includes(extOf(p))) paths.push(p);
      else ignored++;
    }
    if (paths.length > 0) addFiles(paths);
    setNotice(
      ignored > 0 ? `${ignored} dropped item(s) were not video files and were ignored.` : paths.length === 0 ? "Drop video files here." : null,
    );
  }

  async function start() {
    if (running || files.length === 0) return;
    cancelRequested.current = false;
    trashForRun.current = trashAfter;
    runActive.current = true;
    setRunning(true);
    setCancelling(false);
    setRows([]);
    setSummary(null);
    setOverall(null);
    setCurrent(null);

    const args = ["convert", ...files.flatMap((p) => ["--input", p]), "--mode", mode];
    if (mode === "reencode") args.push("--encoder", encoder);
    if (outMode === "folder") args.push("--out-dir", outDir);

    const res = await runCli(args);
    // Let pending Recycle Bin moves finish before the run is reported as done.
    await trashChain.current;
    runActive.current = false;
    setRunning(false);
    setCancelling(false);
    setOverall(null);
    setCurrent(null);

    const err = engineError(res, cancelRequested.current);
    if (err) report(err);
    const done = findEvent(res, "convert_done");
    setSummary(
      cancelRequested.current
        ? "Conversion cancelled."
        : done
          ? `Converted ${done.converted}, failed ${done.failed}, skipped ${done.skipped}.`
          : err
            ? "Conversion failed."
            : "Conversion finished.",
    );
  }

  function cancel() {
    cancelRequested.current = true;
    setCancelling(true);
    void window.api.cancel();
  }

  const outMissing = outMode === "folder" && outDir === "";
  const canConvert = !running && files.length > 0 && !outMissing;
  const pct = overall ? fmtPercent(overall.done, overall.total) : 0;

  return (
    <div className="screen">
      <h2>Convert to MP4</h2>

      <div className="card">
        <div className="row between">
          <h3>Files ({files.length})</h3>
          <div className="row">
            <button onClick={() => void addViaDialog()} disabled={running}>
              Add files...
            </button>
            <button onClick={() => void addFromScan()} disabled={running}>
              Add .ts from last scan
            </button>
            <button onClick={() => setFiles([])} disabled={running || files.length === 0}>
              Clear
            </button>
          </div>
        </div>
        <div
          className={`dropzone ${dragOver ? "over" : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={onDrop}
        >
          Drag video files here to add them
        </div>
        {notice && <p className="note">{notice}</p>}
        {files.length === 0 ? (
          <p className="muted">No files yet.</p>
        ) : (
          <ul className="list">
            {files.map((f) => (
              <li key={f}>
                <span className="mono ellipsis" title={f}>
                  {f}
                </span>
                <button
                  className="danger"
                  disabled={running}
                  onClick={() => setFiles((prev) => prev.filter((x) => x !== f))}
                >
                  Remove
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="card">
        <h3>Options</h3>

        <div className="option-group">
          <span className="muted">Mode</span>
          <label className="row">
            <input type="radio" name="convert-mode" checked={mode === "copy"} disabled={running} onChange={() => setMode("copy")} />
            Copy (fast, no quality loss)
          </label>
          <label className="row">
            <input
              type="radio"
              name="convert-mode"
              checked={mode === "reencode"}
              disabled={running}
              onChange={() => setMode("reencode")}
            />
            Re-encode
          </label>
          {mode === "reencode" && (
            <label className="row">
              Encoder
              <select
                value={encoder}
                disabled={running}
                onChange={(e) => setEncoder(e.target.value as Encoder)}
              >
                <option value="auto">Auto</option>
                <option value="nvenc">NVIDIA GPU (nvenc)</option>
                <option value="x264">CPU (x264)</option>
              </select>
            </label>
          )}
        </div>

        <div className="option-group">
          <span className="muted">Output</span>
          <label className="row">
            <input
              type="radio"
              name="convert-out"
              checked={outMode === "source"}
              disabled={running}
              onChange={() => setOutMode("source")}
            />
            Next to source
          </label>
          <div className="row">
            <label className="row">
              <input
                type="radio"
                name="convert-out"
                checked={outMode === "folder"}
                disabled={running}
                onChange={() => setOutMode("folder")}
              />
              Folder:
            </label>
            <span className="mono ellipsis" title={outDir}>
              {outDir || <span className="muted">none chosen</span>}
            </span>
            <button disabled={running} onClick={() => void chooseOutDir()}>
              Choose...
            </button>
          </div>
        </div>

        <label className="row">
          <input
            type="checkbox"
            checked={trashAfter}
            disabled={running}
            onChange={(e) => setTrashAfter(e.target.checked)}
          />
          Move original to Recycle Bin after successful conversion
        </label>
        {trashAfter && (
          <p className="warn">Each original is moved to the Recycle Bin only after its MP4 is verified.</p>
        )}
      </div>

      <div className="row">
        <button className="primary" disabled={!canConvert} onClick={() => void start()}>
          Convert{files.length > 0 ? ` ${files.length} file(s)` : ""}
        </button>
        {running && (
          <button disabled={cancelling} onClick={cancel}>
            {cancelling ? "Cancelling..." : "Cancel"}
          </button>
        )}
      </div>
      {outMissing && <p className="warn">Choose an output folder, or switch to Next to source.</p>}

      {running && (
        <div className="card">
          <div>{overall ? `${overall.done} / ${overall.total} file(s) done (${pct}%)` : "Starting..."}</div>
          <div className="bar">
            <div style={{ width: `${pct}%` }} />
          </div>
          {current && (
            <>
              <div className="mono ellipsis" title={current.path}>
                {baseName(current.path)}
              </div>
              <div className="bar">
                <div style={{ width: `${current.percent}%` }} />
              </div>
              <div className="muted">{current.percent}%</div>
            </>
          )}
        </div>
      )}

      {summary && <p>{summary}</p>}

      {rows.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>Input</th>
              <th>Output</th>
              <th>Size</th>
              <th>Encoder</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.input}>
                <td className="mono" title={r.input}>
                  {baseName(r.input)}
                </td>
                <td className="mono" title={r.output}>
                  {r.output ? baseName(r.output) : "-"}
                </td>
                <td>{r.sizeIn !== undefined && r.sizeOut !== undefined ? `${fmtMB(r.sizeIn)} → ${fmtMB(r.sizeOut)}` : "-"}</td>
                <td>{r.encoder ?? "-"}</td>
                <td>
                  {r.status === "converted" && r.trash === "pending" && "Converted, moving original to Recycle Bin..."}
                  {r.status === "converted" && r.trash === "recycled" && "Converted, original Recycled"}
                  {r.status === "converted" && r.trash === "failed" && (
                    <span className="text-danger">Converted, Recycle Bin failed: {r.message}</span>
                  )}
                  {r.status === "converted" && r.trash === undefined && "Converted"}
                  {r.status === "error" && <span className="text-danger">Failed: {r.message}</span>}
                  {r.status === "skipped" && <span className="muted">Skipped: {r.message}</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
