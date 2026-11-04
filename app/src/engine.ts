import type { EngineEvent, RunResult } from "./types";

// Runs one engine command through the preload bridge. Never rejects.
export async function runCli(args: string[]): Promise<RunResult> {
  try {
    return await window.api.run(args);
  } catch (e) {
    return { code: -1, events: [{ type: "error", message: String(e) }] };
  }
}

// Returns the first event of the given type, if any.
export function findEvent<T extends EngineEvent["type"]>(
  res: RunResult,
  type: T,
): Extract<EngineEvent, { type: T }> | undefined {
  return res.events.find((e) => e.type === type) as Extract<EngineEvent, { type: T }> | undefined;
}

// Text for the error banner, or null when the command succeeded.
// A non-zero exit is not reported when the user cancelled the command.
export function engineError(res: RunResult, cancelled = false): string | null {
  const messages = res.events
    .filter((e): e is Extract<EngineEvent, { type: "error" }> => e.type === "error")
    .map((e) => e.message);
  if (messages.length > 0) return messages.join("\n");
  if (res.code !== 0 && !cancelled) {
    const stderr = res.events.find((e): e is Extract<EngineEvent, { type: "stderr" }> => e.type === "stderr");
    return `Engine exited with code ${res.code}${stderr ? `\n${stderr.text}` : ""}`;
  }
  return null;
}
