import { useEffect, useRef, useState } from "react";
import { engineError, findEvent, runCli } from "../engine";
import { fmtPercent } from "../format";
import type { ScanStats } from "../types";

interface Props {
  roots: string[];
  workers: number;
  onWorkersChange: (workers: number) => void;
  report: (msg: string) => void;
}

interface Progress {
  done: number;
  total: number;
  path: string;
}

export default function Scan({ roots, workers, onWorkersChange, report }: Props) {
  const [running, setRunning] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [found, setFound] = useState<number | null>(null);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [fileErrors, setFileErrors] = useState<{ path: string; error: string }[]>([]);
  const [summary, setSummary] = useState<ScanStats | null>(null);
  const [outcome, setOutcome] = useState<string | null>(null);
  const [fresh, setFresh] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [cacheNote, setCacheNote] = useState<string | null>(null);
  const cancelRequested = useRef(false);

  useEffect(
    () =>
      window.api.onEvent((ev) => {
        if (ev.type === "files_found") setFound(ev.count);
        else if (ev.type === "progress" && ev.phase === "fingerprint")
          setProgress({ done: ev.done, total: ev.total, path: ev.path });
        else if (ev.type === "file_error")
          setFileErrors((prev) => [...prev, { path: ev.path, error: ev.error }]);
        else if (ev.type === "scan_done")
          setSummary({
            files: ev.files,
            cached: ev.cached,
            unreadable: ev.unreadable,
            groups: ev.groups,
            duplicates: ev.duplicates,
          });
      }),
    [],
  );

  async function start() {
    if (roots.length === 0) {
      report("Add at least one folder on the Folders tab first.");
      return;
    }
    cancelRequested.current = false;
    setRunning(true);
    setCancelling(false);
    setFound(null);
    setProgress(null);
    setFileErrors([]);
    setSummary(null);
    setOutcome(null);

    const args = ["scan", "--workers", String(workers), ...roots.flatMap((r) => ["--root", r])];
    if (fresh) args.push("--fresh");
    const res = await runCli(args);

    setRunning(false);
    setCancelling(false);
    setProgress(null);
    const err = engineError(res, cancelRequested.current);
    if (err) report(err);
    setOutcome(cancelRequested.current ? "Scan cancelled." : err ? "Scan failed." : "Scan finished.");
  }

  function cancel() {
    cancelRequested.current = true;
    setCancelling(true);
    void window.api.cancel();
  }

  async function clearCache() {
    const ok = window.confirm(
      "Clear the scan cache? This forgets fingerprints, video metadata and thumbnails. History and Undo are kept. The next scan re-reads every file (slow on large folders).",
    );
    if (!ok) return;
    setClearing(true);
    setCacheNote(null);
    const res = await runCli(["clear-cache"]);
    setClearing(false);
    const err = engineError(res);
    if (err) {
      report(err);
      return;
    }
    const ev = findEvent(res, "cache_cleared");
    if (ev) {
      setCacheNote(
        `Cache cleared: ${ev.fingerprints} fingerprints, ${ev.files_reset} files reset, ${ev.thumbs_deleted} thumbnails deleted.`,
      );
    }
  }

  const pct = progress ? fmtPercent(progress.done, progress.total) : 0;

  return (
    <div className="screen">
      <h2>Scan</h2>
      <p className="muted">{roots.length} folder(s) will be scanned.</p>

      <div className="row">
        <label>
          Workers per drive{" "}
          <input
            type="number"
            min={1}
            max={16}
            value={workers}
            disabled={running}
            style={{ width: "4em" }}
            onChange={(e) => {
              const n = Math.round(Number(e.target.value));
              if (Number.isFinite(n)) onWorkersChange(Math.min(16, Math.max(1, n)));
            }}
          />
        </label>
        <span className="muted">HDD: 4 · SSD/NVMe: 6–8 · more is not always faster</span>
      </div>

      <div className="row">
        <button className="primary" disabled={running || roots.length === 0} onClick={start}>
          Start scan
        </button>
        {running && (
          <button disabled={cancelling} onClick={cancel}>
            {cancelling ? "Cancelling..." : "Cancel"}
          </button>
        )}
      </div>

      <div className="row">
        <label className="row">
          <input type="checkbox" checked={fresh} disabled={running} onChange={(e) => setFresh(e.target.checked)} />
          Fresh scan (ignore cache)
        </label>
        <button disabled={running || clearing} onClick={() => void clearCache()}>
          {clearing ? "Clearing..." : "Clear cache…"}
        </button>
      </div>
      {cacheNote && <p className="muted">{cacheNote}</p>}

      {running && (
        <div className="card">
          <div>{found === null ? "Listing files..." : `${found} video file(s) found`}</div>
          {progress && (
            <>
              <div className="bar">
                <div style={{ width: `${pct}%` }} />
              </div>
              <div>
                Fingerprinting {progress.done} / {progress.total} ({pct}%)
              </div>
              <div className="mono ellipsis" title={progress.path}>
                {progress.path}
              </div>
            </>
          )}
        </div>
      )}

      {outcome && <p>{outcome}</p>}

      {summary && (
        <div className="card">
          <h3>Summary</h3>
          <div className="stats">
            <div>
              <span className="stat-value">{summary.files}</span>
              <span className="muted">files</span>
            </div>
            <div>
              <span className="stat-value">{summary.cached}</span>
              <span className="muted">cached</span>
            </div>
            <div>
              <span className="stat-value">{summary.unreadable}</span>
              <span className="muted">unreadable</span>
            </div>
            <div>
              <span className="stat-value">{summary.groups}</span>
              <span className="muted">groups</span>
            </div>
            <div>
              <span className="stat-value">{summary.duplicates}</span>
              <span className="muted">duplicates</span>
            </div>
          </div>
        </div>
      )}

      {fileErrors.length > 0 && (
        <div className="card">
          <h3>{fileErrors.length} file(s) could not be read</h3>
          <ul className="errors">
            {fileErrors.slice(-100).map((fe, i) => (
              <li key={`${fe.path}-${i}`}>
                <span className="mono">{fe.path}</span>
                <span className="muted"> {fe.error}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
