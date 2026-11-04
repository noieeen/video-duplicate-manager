import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { engineError, findEvent, runCli } from "../engine";
import { fmtDuration, fmtKbps, fmtMB, fmtResolution } from "../format";
import type { Group, GroupStatus, Member, Role } from "../types";

type Filter = GroupStatus;
type Sort = "space" | "newest";

const FILTERS: Filter[] = ["pending", "confirmed", "applied", "dismissed"];
const PAGE = 50;
const TYPING_TAGS = ["INPUT", "SELECT", "TEXTAREA"];

interface Props {
  active: boolean;
  report: (msg: string) => void;
}

// Bytes freed by removing the group's duplicates.
function reclaimable(g: Group): number {
  return g.members.filter((m) => m.role === "duplicate").reduce((sum, m) => sum + m.size, 0);
}

function fmtGB(bytes: number): string {
  return (bytes / 1e9).toFixed(1);
}

function baseName(p: string): string {
  return p.split(/[\\/]/).pop() ?? p;
}

function canConfirm(g: Group): boolean {
  return g.status !== "applied" && g.status !== "confirmed";
}

function canDismiss(g: Group): boolean {
  return g.status !== "applied" && g.status !== "dismissed";
}

export default function Review({ active, report }: Props) {
  const [filter, setFilter] = useState<Filter>("pending");
  const [sort, setSort] = useState<Sort>("space");
  const [groups, setGroups] = useState<Group[]>([]);
  const [visible, setVisible] = useState(PAGE);
  const [selected, setSelected] = useState(0);
  const [compareId, setCompareId] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const selectedRef = useRef<HTMLDivElement | null>(null);

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
      const next = findEvent(res, "groups")?.groups ?? [];
      setGroups(next);
      setVisible(PAGE);
      setSelected((s) => Math.max(0, Math.min(s, next.length - 1)));
    },
    [report],
  );

  // Reload whenever the tab becomes visible or the filter changes.
  useEffect(() => {
    if (active) void load(filter);
  }, [active, filter, load]);

  function changeFilter(f: Filter) {
    setFilter(f);
    setSelected(0);
  }

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

  const sorted = useMemo(() => {
    const list = [...groups];
    if (sort === "space") list.sort((a, b) => reclaimable(b) - reclaimable(a) || b.id - a.id);
    else list.sort((a, b) => b.id - a.id);
    return list;
  }, [groups, sort]);

  const totalReclaim = useMemo(() => groups.reduce((sum, g) => sum + reclaimable(g), 0), [groups]);

  // The page never hides the selected card, even after a reload reset it to PAGE.
  const shown = Math.max(visible, selected + 1);
  const page = sorted.slice(0, shown);
  const remaining = sorted.length - shown;

  const compareGroup = compareId === null ? undefined : groups.find((g) => g.id === compareId);
  const current = sorted[selected];

  function move(delta: number) {
    if (sorted.length === 0) return;
    const next = Math.max(0, Math.min(sorted.length - 1, selected + delta));
    setSelected(next);
    if (next >= shown) setVisible(shown + PAGE);
  }

  // Keyboard shortcuts. Re-registered on every render so the handler always sees current state.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (compareGroup) {
        if (e.key === "Escape") setCompareId(null);
        return;
      }
      if (!active || busy || e.ctrlKey || e.metaKey || e.altKey) return;
      const target = e.target as HTMLElement | null;
      if (target && (TYPING_TAGS.includes(target.tagName) || target.isContentEditable)) return;
      switch (e.key) {
        case "j":
        case "ArrowDown":
          e.preventDefault();
          move(1);
          break;
        case "k":
        case "ArrowUp":
          e.preventDefault();
          move(-1);
          break;
        case "c":
          if (current && canConfirm(current)) void act(["confirm", "--group", String(current.id)]);
          break;
        case "d":
          if (current && canDismiss(current)) void act(["dismiss", "--group", String(current.id)]);
          break;
        case "x":
          if (current && current.members.length >= 2) setCompareId(current.id);
          break;
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  useEffect(() => {
    selectedRef.current?.scrollIntoView({ block: "nearest" });
  }, [selected]);

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
      <p className="muted review-hint">Keys: j/k move · c confirm · d dismiss · x compare</p>

      <div className="tabs sub">
        {FILTERS.map((f) => (
          <button key={f} className={`tab ${filter === f ? "active" : ""}`} onClick={() => changeFilter(f)}>
            {f}
          </button>
        ))}
      </div>

      <div className="row between">
        <p className="muted">
          {sorted.length} group(s) · {fmtGB(totalReclaim)} GB reclaimable
        </p>
        <label className="row muted">
          Sort:{" "}
          <select value={sort} onChange={(e) => setSort(e.target.value as Sort)}>
            <option value="space">Space saved</option>
            <option value="newest">Newest group id</option>
          </select>
        </label>
      </div>

      {loading && groups.length === 0 && <p className="muted">Loading groups...</p>}
      {!loading && groups.length === 0 && <p className="muted">No {filter} groups.</p>}

      {page.map((g, i) => (
        <GroupCard
          key={g.id}
          group={g}
          busy={busy}
          selected={i === selected}
          cardRef={i === selected ? selectedRef : undefined}
          onRole={(fileId, role) =>
            void act(["set-role", "--group", String(g.id), "--file", String(fileId), "--role", role])
          }
          onConfirm={() => void act(["confirm", "--group", String(g.id)])}
          onDismiss={() => void act(["dismiss", "--group", String(g.id)])}
          onCompare={() => setCompareId(g.id)}
          onShow={(p) => void window.api.showInFolder(p)}
        />
      ))}

      {remaining > 0 && (
        <div className="row">
          <button onClick={() => setVisible(shown + PAGE)}>Show 50 more ({remaining} left)</button>
        </div>
      )}

      {compareGroup && (
        <CompareModal group={compareGroup} report={report} onClose={() => setCompareId(null)} />
      )}
    </div>
  );
}

interface CardProps {
  group: Group;
  busy: boolean;
  selected: boolean;
  cardRef?: RefObject<HTMLDivElement | null>;
  onRole: (fileId: number, role: Role) => void;
  onConfirm: () => void;
  onDismiss: () => void;
  onCompare: () => void;
  onShow: (path: string) => void;
}

function GroupCard({ group, busy, selected, cardRef, onRole, onConfirm, onDismiss, onCompare, onShow }: CardProps) {
  const applied = group.status === "applied";
  const locked = busy || applied;
  return (
    <div ref={cardRef} className={`card group ${selected ? "selected" : ""}`}>
      <div className="row between">
        <div>
          <strong>Group #{group.id}</strong>{" "}
          <span className="muted">
            {group.members.length} file(s)
          </span>
        </div>
        <div className="row">
          <span className={`badge status-${group.status}`}>{group.status}</span>
          <button onClick={onCompare} disabled={group.members.length < 2}>
            Compare
          </button>
          <button onClick={onConfirm} disabled={locked || !canConfirm(group)}>
            Confirm
          </button>
          <button onClick={onDismiss} disabled={locked || !canDismiss(group)}>
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

function openInPlayer(path: string, report: (msg: string) => void) {
  // openPath resolves to an error string, or "" on success.
  void window.api.openPath(path).then((err) => {
    if (err) report(err);
  });
}

function optionLabel(m: Member): string {
  return `${baseName(m.path)} · ${fmtResolution(m.width, m.height)} · ${fmtMB(m.size)}`;
}

function CompareModal({
  group,
  report,
  onClose,
}: {
  group: Group;
  report: (msg: string) => void;
  onClose: () => void;
}) {
  // Left: the keeper (or the first member). Right: the first other member.
  const left0 = group.members.find((m) => m.role === "keeper") ?? group.members[0];
  const right0 = group.members.find((m) => m.file_id !== left0.file_id) ?? group.members[1];
  const [leftId, setLeftId] = useState(left0.file_id);
  const [rightId, setRightId] = useState(right0.file_id);
  const [failed, setFailed] = useState<number[]>([]);
  const leftRef = useRef<HTMLVideoElement | null>(null);
  const rightRef = useRef<HTMLVideoElement | null>(null);

  const left = group.members.find((m) => m.file_id === leftId) ?? left0;
  const right = group.members.find((m) => m.file_id === rightId) ?? right0;

  function markFailed(fileId: number) {
    setFailed((prev) => (prev.includes(fileId) ? prev : [...prev, fileId]));
  }

  function playBoth() {
    for (const v of [leftRef.current, rightRef.current]) {
      // Play can reject (e.g. a format that cannot decode); the error event already covers that case.
      void v?.play().catch(() => {});
    }
  }

  function pauseBoth() {
    leftRef.current?.pause();
    rightRef.current?.pause();
  }

  function syncRight() {
    if (leftRef.current && rightRef.current) rightRef.current.currentTime = leftRef.current.currentTime;
  }

  return (
    <div className="compare-backdrop" role="dialog" aria-modal="true" aria-label="Compare group">
      <div className="compare-modal">
        <div className="row between">
          <strong>
            Compare · Group #{group.id} <span className="muted">(Esc to close)</span>
          </strong>
          <button onClick={onClose}>Close</button>
        </div>

        <div className="row">
          <button onClick={playBoth}>Play both</button>
          <button onClick={pauseBoth}>Pause both</button>
          <button onClick={syncRight}>Sync right to left</button>
        </div>

        <div className="compare-panes">
          <ComparePane
            member={left}
            members={group.members}
            onPick={setLeftId}
            videoRef={leftRef}
            failed={failed.includes(left.file_id)}
            onFail={markFailed}
            report={report}
          />
          <ComparePane
            member={right}
            members={group.members}
            onPick={setRightId}
            videoRef={rightRef}
            failed={failed.includes(right.file_id)}
            onFail={markFailed}
            report={report}
          />
        </div>
      </div>
    </div>
  );
}

function ComparePane({
  member: m,
  members,
  onPick,
  videoRef,
  failed,
  onFail,
  report,
}: {
  member: Member;
  members: Member[];
  onPick: (fileId: number) => void;
  videoRef: RefObject<HTMLVideoElement | null>;
  failed: boolean;
  onFail: (fileId: number) => void;
  report: (msg: string) => void;
}) {
  return (
    <div className="compare-pane">
      <select className="compare-pick" value={m.file_id} onChange={(e) => onPick(Number(e.target.value))}>
        {members.map((x) => (
          <option key={x.file_id} value={x.file_id}>
            {optionLabel(x)}
          </option>
        ))}
      </select>

      {failed ? (
        <div className="compare-fallback">
          <p>This format can&apos;t play in the app</p>
          <button onClick={() => openInPlayer(m.path, report)}>Open in player</button>
        </div>
      ) : (
        <video
          ref={videoRef}
          className="compare-video"
          src={window.api.mediaUrl(m.path)}
          controls
          muted
          preload="metadata"
          onError={() => onFail(m.file_id)}
        />
      )}

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
    </div>
  );
}
