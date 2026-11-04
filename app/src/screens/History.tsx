import { useCallback, useEffect, useState } from "react";
import { engineError, findEvent, runCli } from "../engine";
import { fmtDate, fmtGB } from "../format";
import type { BatchInfo } from "../types";

interface Props {
  active: boolean;
  report: (msg: string) => void;
}

// A batch whose files all went to the Recycle Bin: the engine cannot undo it.
function trashOnly(b: BatchInfo): boolean {
  return b.trashed > 0 && b.moved === 0;
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
              <th>Freed</th>
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
                <td>
                  {b.moved}
                  {b.trashed > 0 && <div className="muted">{b.trashed} in Recycle Bin</div>}
                </td>
                <td>{b.restored}</td>
                <td>{b.failed}</td>
                <td>{fmtGB(b.bytes)}</td>
                <td>
                  {trashOnly(b) ? (
                    <button disabled title="Files are in the Recycle Bin; restore them from there">
                      Undo
                    </button>
                  ) : (
                    <button disabled={busy || b.moved <= b.restored} onClick={() => void undo(b)}>
                      Undo
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
