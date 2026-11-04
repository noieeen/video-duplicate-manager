import { useEffect, useRef, useState } from "react";
import { engineError, runCli } from "../engine";
import { fmtPercent } from "../format";
import type { ScanStats } from "../types";

interface Props {
  roots: string[];
  report: (msg: string) => void;
}

interface Progress {
  done: number;
  total: number;
  path: string;
}

export default function Scan({ roots, report }: Props) {
  const [running, setRunning] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [found, setFound] = useState<number | null>(null);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [fileErrors, setFileErrors] = useState<{ path: string; error: string }[]>([]);
  const [summary, setSummary] = useState<ScanStats | null>(null);
  const [outcome, setOutcome] = useState<string | null>(null);
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

    const res = await runCli(["scan", ...roots.flatMap((r) => ["--root", r])]);

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

  const pct = progress ? fmtPercent(progress.done, progress.total) : 0;

  return (
    <div className="screen">
      <h2>Scan</h2>
      <p className="muted">{roots.length} folder(s) will be scanned.</p>

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
