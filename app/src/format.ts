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

// Decimal units, matching the GB figures the engine reports.
export function fmtSize(bytes: number): string {
  if (bytes >= 1e12) return `${(bytes / 1e12).toFixed(2)} TB`;
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(1)} GB`;
  return `${Math.round(bytes / 1e6)} MB`;
}

export function fmtDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export function fmtPercent(done: number, total: number): number {
  return total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0;
}
