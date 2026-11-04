import { useCallback, useEffect, useState } from "react";
import { engineError, findEvent, runCli } from "../engine";
import { fmtPercent } from "../format";
import { isInside } from "../paths";
import type { Group, Settings } from "../types";

interface Props {
  active: boolean;
  settings: Settings;
  report: (msg: string) => void;
}

interface Progress {
  done: number;
  total: number;
  path: string;
}

interface ApplyResult {
  batch_id: string;
  moved: number;
  failed: number;
}

export default function Apply({ active, settings, report }: Props) {
  const { dupRoot, roots } = settings;
  const [groups, setGroups] = useState<Group[]>([]);
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [result, setResult] = useState<ApplyResult | null>(null);

  const loadConfirmed = useCallback(async () => {
    const res = await runCli(["groups", "--status", "confirmed"]);
    const err = engineError(res);
    if (err) {
      report(err);
      return;
    }
    setGroups(findEvent(res, "groups")?.groups ?? []);
  }, [report]);

  useEffect(() => {
    if (active) void loadConfirmed();
  }, [active, loadConfirmed]);

  useEffect(
    () =>
      window.api.onEvent((ev) => {
        if (ev.type === "progress" && ev.phase === "apply") {
          setProgress({ done: ev.done, total: ev.total, path: ev.path });
        }
      }),
    [],
  );

  const groupCount = groups.length;
  const duplicateFiles = groups.reduce((n, g) => n + g.members.filter((m) => m.role === "duplicate").length, 0);
  const dupInsideRoot = roots.find((r) => isInside(dupRoot, r));
  const pct = progress ? fmtPercent(progress.done, progress.total) : 0;

  async function apply() {
    const ok = window.confirm(
      `Move ${duplicateFiles} duplicate file(s) from ${groupCount} confirmed group(s) into:\n\n${dupRoot}\n\n` +
        "Files are moved, never deleted, and any batch can be undone from the History tab. Continue?",
    );
    if (!ok) return;

    setRunning(true);
    setResult(null);
    setProgress(null);
    const res = await runCli(["apply", "--dup-root", dupRoot]);
    setRunning(false);
    setProgress(null);

    const err = engineError(res);
    if (err) report(err);
    const done = findEvent(res, "apply_done");
    if (done) setResult({ batch_id: done.batch_id, moved: done.moved, failed: done.failed });
    await loadConfirmed();
  }

  return (
    <div className="screen">
      <h2>Apply</h2>

      <div className="card">
        <div className="stats">
          <div>
            <span className="stat-value">{groupCount}</span>
            <span className="muted">confirmed groups</span>
          </div>
          <div>
            <span className="stat-value">{duplicateFiles}</span>
            <span className="muted">duplicate files</span>
          </div>
        </div>
        <p>
          Target: {dupRoot ? <span className="mono">{dupRoot}</span> : <span className="warn">not set</span>}
        </p>
        {!dupRoot && <p className="warn">Choose a duplicates folder on the Folders tab first.</p>}
        {dupInsideRoot && (
          <p className="warn">The duplicates folder is inside a scan root ({dupInsideRoot}).</p>
        )}
        {groupCount === 0 && (
          <p className="muted">No confirmed groups. Confirm groups on the Review tab first.</p>
        )}
        <button
          className="primary"
          disabled={running || !dupRoot || groupCount === 0}
          onClick={() => void apply()}
        >
          Apply {groupCount} confirmed group(s)
        </button>
      </div>

      {running && (
        <div className="card">
          <div>Moving files...</div>
          {progress && (
            <>
              <div className="bar">
                <div style={{ width: `${pct}%` }} />
              </div>
              <div>
                {progress.done} / {progress.total} ({pct}%)
              </div>
              <div className="mono ellipsis" title={progress.path}>
                {progress.path}
              </div>
            </>
          )}
        </div>
      )}

      {result && (
        <div className="card">
          <h3>Apply finished</h3>
          <p>
            Moved {result.moved}, failed {result.failed}.
          </p>
          <p className="muted mono">Batch {result.batch_id}</p>
        </div>
      )}
    </div>
  );
}
