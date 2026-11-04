export function fmtDuration(sec: number | null): string {
  if (sec === null || !Number.isFinite(sec)) return "--:--";
  const total = Math.round(sec);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

export function fmtResolution(w: number | null, h: number | null): string {
  return w && h ? `${w}x${h}` : "-";
}

export function fmtKbps(bitsPerSec: number | null): string {
  return bitsPerSec ? `${Math.round(bitsPerSec / 1000)} kbps` : "-";
}

export function fmtMB(bytes: number): string {
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function fmtGB(bytes: number | null | undefined): string {
  return bytes === null || bytes === undefined ? "-" : `${(bytes / 1e9).toFixed(1)} GB`;
}

export function fmtDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export function fmtPercent(done: number, total: number): number {
  return total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0;
}
