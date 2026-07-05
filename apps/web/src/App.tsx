import { useState } from "react";
import { ExplorePage } from "./features/explore/ExplorePage";

type View = "explore" | "settings";

export function App() {
  const [view, setView] = useState<View>("explore");

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          Open<span>Earth</span>
        </div>
        <nav>
          <button className={view === "explore" ? "active" : ""} onClick={() => setView("explore")}>
            Explore
          </button>
          <button
            className={view === "settings" ? "active" : ""}
            onClick={() => setView("settings")}
          >
            Settings
          </button>
        </nav>
      </header>
      <main className="main">
        {view === "explore" ? (
          <ExplorePage />
        ) : (
          <aside className="side-panel" style={{ width: "100%" }}>
            <div className="panel-section">
              <h3>Settings</h3>
              <p className="muted">EE status, cache stats, and custom datasets arrive soon.</p>
            </div>
          </aside>
        )}
      </main>
    </div>
  );
}
