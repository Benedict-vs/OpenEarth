import { useState } from "react";
import { CompareView } from "./features/compare/CompareView";
import { ExplorePage } from "./features/explore/ExplorePage";
import { WorkspaceMenu } from "./features/explore/WorkspaceMenu";
import { MethanePage } from "./features/methane/MethanePage";
import { SettingsPage } from "./features/settings/SettingsPage";
import { TimelapsePage } from "./features/timelapse/TimelapsePage";

type View = "explore" | "compare" | "methane" | "timelapse" | "settings";

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
          <button className={view === "compare" ? "active" : ""} onClick={() => setView("compare")}>
            Compare
          </button>
          <button className={view === "methane" ? "active" : ""} onClick={() => setView("methane")}>
            Methane Lab
          </button>
          <button
            className={view === "timelapse" ? "active" : ""}
            onClick={() => setView("timelapse")}
          >
            Timelapse
          </button>
          <button
            className={view === "settings" ? "active" : ""}
            onClick={() => setView("settings")}
          >
            Settings
          </button>
        </nav>
        {view === "explore" ? <WorkspaceMenu /> : null}
      </header>
      <main className="main">
        {view === "explore" ? <ExplorePage /> : null}
        {view === "compare" ? <CompareView /> : null}
        {view === "methane" ? <MethanePage /> : null}
        {view === "timelapse" ? <TimelapsePage /> : null}
        {view === "settings" ? <SettingsPage /> : null}
      </main>
    </div>
  );
}
