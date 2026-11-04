// Windows paths: compare case-insensitively and ignore slash style and trailing slashes.
function pathKey(p: string): string {
  return p.replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();
}

export function samePath(a: string, b: string): boolean {
  return pathKey(a) === pathKey(b);
}

// True when child is parent itself or somewhere below it.
export function isInside(child: string, parent: string): boolean {
  const c = pathKey(child);
  const p = pathKey(parent);
  if (!p || !c) return false;
  return c === p || c.startsWith(`${p}/`);
}
