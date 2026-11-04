import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent } from "react";
import { engineError, findEvent, runCli } from "../engine";
import { fmtResolution, fmtSize } from "../format";
import type { EngineEvent, LibraryFile, LibraryGroup } from "../types";

type GroupBy = "potential" | "resolution" | "codec" | "folder";
type Summary = Extract<EngineEvent, { type: "library" }>;

const GROUP_OPTIONS: { id: GroupBy; label: string }[] = [
  { id: "potential", label: "Space-saving potential" },
  { id: "resolution", label: "Resolution" },
  { id: "codec", label: "Codec" },
  { id: "folder", label: "Folder" },
];

const PAGE = 200;
const SIZE = 240;
const CENTER = 120;
const RING_OUTER = 110;
const RING_INNER = 66;

interface Props {
  active: boolean;
  roots: string[];
  report: (msg: string) => void;
}

function rootArgs(roots: string[]): string[] {
  return roots.flatMap((r) => ["--root", r]);
}

function seriesColor(index: number): string {
  return `var(--series-${(index % 6) + 1})`;
}

function sharePercent(bytes: number, total: number): string {
  if (total <= 0) return "0.0%";
  const pct = (bytes / total) * 100;
  return bytes > 0 && pct < 0.1 ? "<0.1%" : `${pct.toFixed(1)}%`;
}

function pointAt(radius: number, angle: number): [number, number] {
  return [CENTER + radius * Math.cos(angle), CENTER + radius * Math.sin(angle)];
}

// Ring segment from angle a0 to a1 (radians, clockwise from 12 o'clock).
function slicePath(a0: number, a1: number): string {
  const large = a1 - a0 > Math.PI ? 1 : 0;
  const [ox0, oy0] = pointAt(RING_OUTER, a0);
  const [ox1, oy1] = pointAt(RING_OUTER, a1);
  const [ix1, iy1] = pointAt(RING_INNER, a1);
  const [ix0, iy0] = pointAt(RING_INNER, a0);
  return [
    `M ${ox0} ${oy0}`,
    `A ${RING_OUTER} ${RING_OUTER} 0 ${large} 1 ${ox1} ${oy1}`,
    `L ${ix1} ${iy1}`,
    `A ${RING_INNER} ${RING_INNER} 0 ${large} 0 ${ix0} ${iy0}`,
    "Z",
  ].join(" ");
}

// One list of path strings per group, in group order. Empty for groups with no bytes.
// A group that holds everything is drawn as two half arcs, since an SVG arc cannot span 360 degrees.
function donutSlices(groups: LibraryGroup[], total: number): string[][] {
  let angle = -Math.PI / 2;
  return groups.map((g) => {
    if (total <= 0 || g.bytes <= 0) return [];
    const a0 = angle;
    const a1 = angle + (g.bytes / total) * 2 * Math.PI;
    angle = a1;
    if (a1 - a0 >= 2 * Math.PI - 1e-6) {
      const mid = a0 + Math.PI;
      return [slicePath(a0, mid), slicePath(mid, a0 + 2 * Math.PI)];
    }
    return [slicePath(a0, a1)];
  });
}

export default function Space({ active, roots, report }: Props) {
  const [groupBy, setGroupBy] = useState<GroupBy>("potential");
  const [summary, setSummary] = useState<Summary | null>(null);
  const [loading, setLoading] = useState(false);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [files, setFiles] = useState<LibraryFile[]>([]);
  const [filesTotal, setFilesTotal] = useState(0);
  const [filesLoading, setFilesLoading] = useState(false);

  // Roots as a string so the effect below does not re-run on every settings object identity change.
  const rootsKey = roots.join("\n");
  const rootList = useMemo(() => (rootsKey ? rootsKey.split("\n") : []), [rootsKey]);
  const scope = `${groupBy}\n${rootsKey}`;
  const scopeRef = useRef(scope);
  // Request counters: a response only lands if no newer request of the same kind started.
  const groupReq = useRef(0);
  const fileReq = useRef(0);

  // Drops the selected group and its file list. Setters and refs are stable, so this is safe in effect deps.
  const resetSelection = useCallback(() => {
    fileReq.current++;
    setSelectedKey(null);
    setFiles([]);
    setFilesTotal(0);
    setFilesLoading(false);
  }, []);

  const loadGroups = useCallback(
    async (g: GroupBy, scanRoots: string[]) => {
      const id = ++groupReq.current;
      setLoading(true);
      const res = await runCli(["library", "--group-by", g, ...rootArgs(scanRoots)]);
      if (id !== groupReq.current) return;
      setLoading(false);
      const err = engineError(res);
      if (err) {
        report(err);
        return;
      }
      setSummary(findEvent(res, "library") ?? null);
    },
    [report],
  );

  const loadFiles = useCallback(
    async (g: GroupBy, scanRoots: string[], key: string, offset: number) => {
      const id = ++fileReq.current;
      setFilesLoading(true);
      const res = await runCli([
        "library",
        "--group-by",
        g,
        "--group",
        key,
        "--limit",
        String(PAGE),
        "--offset",
        String(offset),
        ...rootArgs(scanRoots),
      ]);
      if (id !== fileReq.current) return;
      setFilesLoading(false);
      const err = engineError(res);
      if (err) {
        report(err);
        return;
      }
      const ev = findEvent(res, "library_files");
      if (!ev) return;
      setFilesTotal(ev.total);
      setFiles((prev) => (offset === 0 ? ev.files : [...prev, ...ev.files]));
    },
    [report],
  );

  // Loads when the tab becomes visible, and when group-by or the roots change.
  // A scope change also clears the selection, since its file list belongs to the old grouping.
  useEffect(() => {
    if (scopeRef.current !== scope) {
      scopeRef.current = scope;
      resetSelection();
      setSummary(null);
    }
    if (active && rootList.length > 0) void loadGroups(groupBy, rootList);
  }, [active, scope, groupBy, rootList, loadGroups, resetSelection]);

  function toggleGroup(key: string) {
    if (key === selectedKey) {
      resetSelection();
      return;
    }
    setSelectedKey(key);
    setFiles([]);
    setFilesTotal(0);
    void loadFiles(groupBy, rootList, key, 0);
  }

  function showMore() {
    if (selectedKey === null) return;
    void loadFiles(groupBy, rootList, selectedKey, files.length);
  }

  const hasRoots = rootList.length > 0;
  const groups = summary?.groups ?? [];
  const totalBytes = summary?.total_bytes ?? 0;
  const totalFiles = summary?.total_files ?? 0;
  const selectedIndex = selectedKey === null ? -1 : groups.findIndex((g) => g.key === selectedKey);
  const selectedGroup = selectedIndex >= 0 ? groups[selectedIndex] : undefined;
  const remaining = filesTotal - files.length;

  return (
    <div className="screen">
      <div className="row between">
        <h2>Space</h2>
        <div className="row">
          <label className="row muted">
            Group by{" "}
            <select value={groupBy} onChange={(e) => setGroupBy(e.target.value as GroupBy)}>
              {GROUP_OPTIONS.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.label}
                </option>
              ))}
            </select>
          </label>
          <button
            disabled={loading || !hasRoots}
            onClick={() => {
              resetSelection();
              void loadGroups(groupBy, rootList);
            }}
          >
            Refresh
          </button>
        </div>
      </div>

      {!hasRoots && <p className="muted">Add folders on the Folders tab, then run a scan.</p>}
      {hasRoots && loading && !summary && <p className="muted">Loading...</p>}
      {hasRoots && summary && totalFiles === 0 && <p className="muted">No scanned files yet — run a scan first.</p>}

      {hasRoots && summary && totalFiles > 0 && (
        <>
          <div className="card space-overview">
            <Donut
              groups={groups}
              total={totalBytes}
              count={totalFiles}
              onSelect={toggleGroup}
            />
            <div>
              <table className="space-legend-table">
                <thead>
                  <tr>
                    <th>Group</th>
                    <th className="num">Size</th>
                    <th className="num">Share</th>
                    <th className="num">Files</th>
                  </tr>
                </thead>
                <tbody>
                  {groups.map((g, i) => (
                    <tr
                      key={g.key}
                      className={g.key === selectedKey ? "is-selected" : ""}
                      onClick={() => toggleGroup(g.key)}
                    >
                      <td>
                        <button className="space-row-btn" aria-pressed={g.key === selectedKey}>
                          <span className="space-swatch" style={{ backgroundColor: seriesColor(i) }} />
                          {g.label}
                        </button>
                      </td>
                      <td className="num">{fmtSize(g.bytes)}</td>
                      <td className="num">{sharePercent(g.bytes, totalBytes)}</td>
                      <td className="num">{g.files}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {groupBy === "potential" && (
                <p className="muted space-hint">
                  Above 1080p and H.264 over 4 Mb/s are where Shrink (Convert tab) saves the most.
                </p>
              )}
            </div>
          </div>

          <div className="card">
            {!selectedGroup ? (
              <p className="muted">Click a slice to list its files</p>
            ) : (
              <>
                <h3>
                  {selectedGroup.label} — {selectedGroup.files} files, {fmtSize(selectedGroup.bytes)}
                </h3>
                {filesLoading && files.length === 0 && <p className="muted">Loading files...</p>}
                {files.length > 0 && (
                  <div className="space-files-scroll">
                    <table className="space-files-table">
                      <thead>
                        <tr>
                          <th>Path</th>
                          <th className="num">Size</th>
                          <th className="num">Resolution</th>
                          <th>Codec</th>
                          <th className="num">Mb/s</th>
                          <th>Status</th>
                          <th />
                        </tr>
                      </thead>
                      <tbody>
                        {files.map((f) => (
                          <tr key={f.file_id}>
                            <td className="space-path">
                              <a
                                href="#"
                                className="mono ellipsis space-path-link"
                                title={f.path}
                                onClick={(e) => {
                                  e.preventDefault();
                                  void window.api.showInFolder(f.path);
                                }}
                              >
                                {f.path}
                              </a>
                            </td>
                            <td className="num">{fmtSize(f.size)}</td>
                            <td className="num">{fmtResolution(f.width, f.height)}</td>
                            <td>{f.codec ?? "-"}</td>
                            <td className="num">{f.bit_rate ? (f.bit_rate / 1e6).toFixed(1) : "-"}</td>
                            <td>
                              {f.status === "unreadable" && (
                                <span className="badge space-unreadable">unreadable</span>
                              )}
                            </td>
                            <td>
                              <button onClick={() => void window.api.showInFolder(f.path)}>Show in folder</button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                {remaining > 0 && (
                  <div className="row space-more">
                    <button onClick={showMore} disabled={filesLoading}>
                      Show {PAGE} more ({remaining} left)
                    </button>
                  </div>
                )}
              </>
            )}
          </div>
        </>
      )}
    </div>
  );
}

interface DonutProps {
  groups: LibraryGroup[];
  total: number;
  count: number;
  onSelect: (key: string) => void;
}

function Donut({ groups, total, count, onSelect }: DonutProps) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [tip, setTip] = useState<{ i: number; x: number; y: number } | null>(null);
  const slices = useMemo(() => donutSlices(groups, total), [groups, total]);
  const tipGroup = tip ? groups[tip.i] : undefined;

  function moveTo(i: number, e: ReactMouseEvent<SVGPathElement>) {
    const rect = wrapRef.current?.getBoundingClientRect();
    if (!rect) return;
    setTip({ i, x: e.clientX - rect.left, y: e.clientY - rect.top });
  }

  return (
    <div className="space-donut" ref={wrapRef}>
      <svg viewBox={`0 0 ${SIZE} ${SIZE}`} role="img" aria-label="Library space by group">
        {/* Empty track: visible behind the slices and when there is no size to show. */}
        <circle
          cx={CENTER}
          cy={CENTER}
          r={(RING_OUTER + RING_INNER) / 2}
          fill="none"
          style={{ stroke: "var(--border)", strokeWidth: RING_OUTER - RING_INNER }}
        />
        {groups.map((g, i) =>
          slices[i].map((d, j) => {
            const hovered = tip?.i === i;
            return (
              <path
                key={`${g.key}-${j}`}
                d={d}
                className={`space-slice${hovered ? " is-hover" : ""}`}
                style={{ fill: seriesColor(i), stroke: "var(--panel)", strokeWidth: 2 }}
                onMouseEnter={(e) => moveTo(i, e)}
                onMouseMove={(e) => moveTo(i, e)}
                onMouseLeave={() => setTip(null)}
                onClick={() => onSelect(g.key)}
              />
            );
          }),
        )}
        <text x={CENTER} y={CENTER - 2} textAnchor="middle" className="space-center-total">
          {fmtSize(total)}
        </text>
        <text x={CENTER} y={CENTER + 18} textAnchor="middle" className="space-center-files">
          {count} files
        </text>
      </svg>
      {tip && tipGroup && (
        <div
          className="space-tip"
          style={{
            left: tip.x,
            top: tip.y,
            transform: tip.x > SIZE / 2 ? "translate(calc(-100% - 14px), 14px)" : "translate(14px, 14px)",
          }}
        >
          <div className="space-tip-label">{tipGroup.label}</div>
          <div>
            {fmtSize(tipGroup.bytes)} · {sharePercent(tipGroup.bytes, total)}
          </div>
          <div className="muted">{tipGroup.files} files</div>
        </div>
      )}
    </div>
  );
}
