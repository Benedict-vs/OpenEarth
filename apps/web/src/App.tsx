import { useState } from "react";
import { ExplorePage } from "./features/explore/ExplorePage";
import { SettingsPage } from "./features/settings/SettingsPage";

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
      <main className="main">{view === "explore" ? <ExplorePage /> : <SettingsPage />}</main>
    </div>
  );
}
