import { useCallback, useEffect, useState } from "react";
import { engineError, findEvent, runCli } from "../engine";
import { fmtDuration, fmtKbps, fmtMB, fmtResolution } from "../format";
import type { Group, GroupStatus, Member, Role } from "../types";

type Filter = GroupStatus;

const FILTERS: Filter[] = ["pending", "confirmed", "applied", "dismissed"];

interface Props {
  active: boolean;
  report: (msg: string) => void;
}

export default function Review({ active, report }: Props) {
  const [filter, setFilter] = useState<Filter>("pending");
  const [groups, setGroups] = useState<Group[]>([]);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);

  const load = useCallback(
    async (f: Filter) => {
      setLoading(true);
      const res = await runCli(["groups", "--status", f]);
      setLoading(false);
      const err = engineError(res);
      if (err) {
        report(err);
        return;
      }
      setGroups(findEvent(res, "groups")?.groups ?? []);
    },
    [report],
  );

  // Reload whenever the tab becomes visible or the filter changes.
  useEffect(() => {
    if (active) void load(filter);
  }, [active, filter, load]);

  async function act(args: string[]) {
    setBusy(true);
    const res = await runCli(args);
    setBusy(false);
    const err = engineError(res);
    if (err) report(err);
    await load(filter);
  }

  function acceptAll() {
    if (!window.confirm("Confirm every pending group, accepting all suggestions?")) return;
    void act(["confirm", "--all-pending"]);
  }

  return (
    <div className="screen">
      <div className="row between">
        <h2>Review</h2>
        <div className="row">
          <button onClick={() => void load(filter)} disabled={loading || busy}>
            Refresh
          </button>
          <button className="primary" onClick={acceptAll} disabled={busy}>
            Accept all suggestions
          </button>
        </div>
      </div>

      <div className="tabs sub">
        {FILTERS.map((f) => (
          <button key={f} className={`tab ${filter === f ? "active" : ""}`} onClick={() => setFilter(f)}>
            {f}
          </button>
        ))}
      </div>

      {loading && groups.length === 0 && <p className="muted">Loading groups...</p>}
      {!loading && groups.length === 0 && <p className="muted">No {filter} groups.</p>}

      {groups.map((g) => (
        <GroupCard
          key={g.id}
          group={g}
          busy={busy}
          onRole={(fileId, role) =>
            void act(["set-role", "--group", String(g.id), "--file", String(fileId), "--role", role])
          }
          onConfirm={() => void act(["confirm", "--group", String(g.id)])}
          onDismiss={() => void act(["dismiss", "--group", String(g.id)])}
          onShow={(p) => void window.api.showInFolder(p)}
        />
      ))}
    </div>
  );
}

interface CardProps {
  group: Group;
  busy: boolean;
  onRole: (fileId: number, role: Role) => void;
  onConfirm: () => void;
  onDismiss: () => void;
  onShow: (path: string) => void;
}

function GroupCard({ group, busy, onRole, onConfirm, onDismiss, onShow }: CardProps) {
  const applied = group.status === "applied";
  const locked = busy || applied;
  return (
    <div className="card group">
      <div className="row between">
        <div>
          <strong>Group #{group.id}</strong>{" "}
          <span className="muted">
            {group.members.length} file(s)
          </span>
        </div>
        <div className="row">
          <span className={`badge status-${group.status}`}>{group.status}</span>
          <button onClick={onConfirm} disabled={locked || group.status === "confirmed"}>
            Confirm
          </button>
          <button onClick={onDismiss} disabled={locked || group.status === "dismissed"}>
            Dismiss
          </button>
        </div>
      </div>
      {group.members.map((m) => (
        <MemberRow key={m.file_id} member={m} locked={locked} onRole={onRole} onShow={onShow} />
      ))}
    </div>
  );
}

function MemberRow({
  member: m,
  locked,
  onRole,
  onShow,
}: {
  member: Member;
  locked: boolean;
  onRole: (fileId: number, role: Role) => void;
  onShow: (path: string) => void;
}) {
  return (
    <div className={`member role-${m.role}`}>
      <div className="thumb">
        {m.thumb ? <img src={window.api.fileUrl(m.thumb)} alt="" loading="lazy" /> : <span className="muted">no preview</span>}
      </div>
      <div className="meta">
        <div className="mono ellipsis" title={m.path}>
          {m.path}
        </div>
        <div className="facts">
          <span>{fmtResolution(m.width, m.height)}</span>
          <span>{fmtDuration(m.duration)}</span>
          <span>{fmtKbps(m.bit_rate)}</span>
          <span>{fmtMB(m.size)}</span>
          <span className={`badge role-${m.role}`}>{m.role}</span>
        </div>
        <div className="row">
          <button disabled={locked || m.role === "keeper"} onClick={() => onRole(m.file_id, "keeper")}>
            Make keeper
          </button>
          <button disabled={locked || m.role === "ignored"} onClick={() => onRole(m.file_id, "ignored")}>
            Ignore
          </button>
          <button onClick={() => onShow(m.path)}>Show in folder</button>
        </div>
      </div>
    </div>
  );
}
