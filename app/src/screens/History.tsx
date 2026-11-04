import { useCallback, useEffect, useState } from "react";
import { engineError, findEvent, runCli } from "../engine";
import { fmtDate } from "../format";
import type { BatchInfo } from "../types";

interface Props {
  active: boolean;
  report: (msg: string) => void;
}

export default function History({ active, report }: Props) {
  const [batches, setBatches] = useState<BatchInfo[]>([]);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(async () => {
    const res = await runCli(["history"]);
    const err = engineError(res);
    if (err) {
      report(err);
      return;
    }
    setBatches(findEvent(res, "history")?.batches ?? []);
  }, [report]);

  useEffect(() => {
    if (active) void load();
  }, [active, load]);

  async function undo(b: BatchInfo) {
    const ok = window.confirm(
      `Undo batch ${b.batch_id}?\n\nThe moved files go back to their original locations. ` +
        "Nothing is overwritten; a file whose original spot is taken is reported as failed.",
    );
    if (!ok) return;

    setBusy(true);
    setNote(null);
    const res = await runCli(["undo", "--batch", b.batch_id]);
    setBusy(false);
    const err = engineError(res);
    if (err) report(err);
    const done = findEvent(res, "undo_done");
    if (done) setNote(`Batch ${done.batch_id}: restored ${done.restored}, failed ${done.failed}.`);
    await load();
  }

  return (
    <div className="screen">
      <div className="row between">
        <h2>History</h2>
        <button onClick={() => void load()} disabled={busy}>
          Refresh
        </button>
      </div>

      {note && <p className="note">{note}</p>}

      {batches.length === 0 ? (
        <p className="muted">No apply batches yet.</p>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>When</th>
              <th>Batch</th>
              <th>Moved</th>
              <th>Restored</th>
              <th>Failed</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {batches.map((b) => (
              <tr key={b.batch_id}>
                <td>{fmtDate(b.at)}</td>
                <td className="mono" title={b.batch_id}>
                  {b.batch_id.slice(0, 8)}
                </td>
                <td>{b.moved}</td>
                <td>{b.restored}</td>
                <td>{b.failed}</td>
                <td>
                  <button disabled={busy || b.moved <= b.restored} onClick={() => void undo(b)}>
                    Undo
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
