import { useCallback, useEffect, useState } from "react";
import Folders from "./screens/Folders";
import Scan from "./screens/Scan";
import Review from "./screens/Review";
import Apply from "./screens/Apply";
import History from "./screens/History";
import type { Settings } from "./types";

type Tab = "folders" | "scan" | "review" | "apply" | "history";

const TABS: { id: Tab; label: string }[] = [
  { id: "folders", label: "Folders" },
  { id: "scan", label: "Scan" },
  { id: "review", label: "Review" },
  { id: "apply", label: "Apply" },
  { id: "history", label: "History" },
];

export default function App() {
  const [tab, setTab] = useState<Tab>("folders");
  const [settings, setSettings] = useState<Settings | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    window.api
      .getSettings()
      .then(setSettings)
      .catch((e: unknown) => setError(String(e)));
  }, []);

  const report = useCallback((msg: string) => setError(msg), []);

  const updateSettings = useCallback(
    (next: Settings) => {
      setSettings(next);
      window.api.saveSettings(next).catch((e: unknown) => setError(String(e)));
    },
    [],
  );

  // Screens stay mounted and are only hidden, so a running scan keeps its state when switching tabs.
  const show = (id: Tab) => ({ hidden: tab !== id });

  return (
    <div className="app">
      <header className="topbar">
        <h1>Video Duplicate Manager</h1>
        <nav className="tabs">
          {TABS.map((t) => (
            <button key={t.id} className={`tab ${tab === t.id ? "active" : ""}`} onClick={() => setTab(t.id)}>
              {t.label}
            </button>
          ))}
        </nav>
      </header>

      {error && (
        <div className="banner error" role="alert">
          <pre>{error}</pre>
          <button onClick={() => setError(null)}>Dismiss</button>
        </div>
      )}

      <main className="content">
        {!settings ? (
          <p className="muted">Loading...</p>
        ) : (
          <>
            <section {...show("folders")}>
              <Folders settings={settings} onChange={updateSettings} report={report} />
            </section>
            <section {...show("scan")}>
              <Scan roots={settings.roots} report={report} />
            </section>
            <section {...show("review")}>
              <Review active={tab === "review"} report={report} />
            </section>
            <section {...show("apply")}>
              <Apply active={tab === "apply"} settings={settings} report={report} />
            </section>
            <section {...show("history")}>
              <History active={tab === "history"} report={report} />
            </section>
          </>
        )}
      </main>
    </div>
  );
}
