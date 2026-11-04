import { useState, type DragEvent } from "react";
import { isInside, samePath } from "../paths";
import type { Settings } from "../types";

interface Props {
  settings: Settings;
  onChange: (next: Settings) => void;
  report: (msg: string) => void;
}

export default function Folders({ settings, onChange, report }: Props) {
  const [dragOver, setDragOver] = useState(false);
  const dupInsideRoot = settings.roots.find((r) => isInside(settings.dupRoot, r));

  function addRoots(paths: string[]) {
    const next = [...settings.roots];
    for (const p of paths) {
      if (!next.some((r) => samePath(r, p))) next.push(p);
    }
    if (next.length !== settings.roots.length) onChange({ ...settings, roots: next });
  }

  async function addViaDialog() {
    const paths = await window.api.pickFolders();
    if (paths.length > 0) addRoots(paths);
  }

  async function chooseDupRoot() {
    const p = await window.api.pickFolder();
    if (p) onChange({ ...settings, dupRoot: p });
  }

  function onDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragOver(false);
    const paths: string[] = [];
    for (const item of Array.from(e.dataTransfer.items)) {
      if (item.kind !== "file") continue;
      const file = item.getAsFile();
      if (!file) continue;
      const entry = item.webkitGetAsEntry?.();
      if (entry && !entry.isDirectory) continue;
      paths.push(window.api.pathForFile(file));
    }
    if (paths.length > 0) addRoots(paths);
    else report("Drop folders here, not files.");
  }

  return (
    <div className="screen">
      <h2>Folders</h2>

      <div className="card">
        <div className="row between">
          <h3>Scan roots</h3>
          <button onClick={addViaDialog}>Add folder...</button>
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
          Drag folders here to add them
        </div>
        {settings.roots.length === 0 ? (
          <p className="muted">No folders yet.</p>
        ) : (
          <ul className="list">
            {settings.roots.map((r) => (
              <li key={r}>
                <span className="mono ellipsis" title={r}>
                  {r}
                </span>
                <button
                  className="danger"
                  onClick={() => onChange({ ...settings, roots: settings.roots.filter((x) => x !== r) })}
                >
                  Remove
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="card">
        <div className="row between">
          <h3>Duplicates folder</h3>
          <div className="row">
            <button onClick={chooseDupRoot}>Choose...</button>
            {settings.dupRoot && (
              <button onClick={() => onChange({ ...settings, dupRoot: "" })}>Clear</button>
            )}
          </div>
        </div>
        <p className="mono ellipsis" title={settings.dupRoot}>
          {settings.dupRoot || <span className="muted">Not set. Duplicates are moved here on Apply.</span>}
        </p>
        {dupInsideRoot && (
          <p className="warn">
            The duplicates folder is inside a scan root ({dupInsideRoot}). Moved files could be picked up by later
            scans. Choose a folder outside your roots.
          </p>
        )}
      </div>
    </div>
  );
}
