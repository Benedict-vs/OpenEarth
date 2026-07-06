import { useState } from "react";
import { LabMap } from "../../map/LabMap";
import { DetectionDetail } from "./DetectionDetail";
import { DetectionFeed } from "./DetectionFeed";
import { RunPanel } from "./RunPanel";
import { SceneStrip } from "./SceneStrip";
import { ScreeningDialog } from "./ScreeningDialog";
import { SiteList } from "./SiteList";

/** 3-pane Methane Lab: sites/controls | map | detection feed + detail. */
export function MethanePage() {
  const [screening, setScreening] = useState(false);

  return (
    <div className="methane-lab">
      <aside className="lab-left">
        <SiteList onScreen={() => setScreening(true)} />
        <div className="panel-section">
          <h3>Scenes</h3>
          <SceneStrip />
        </div>
        <div className="panel-section">
          <h3>Run</h3>
          <RunPanel />
        </div>
      </aside>

      <div className="lab-center">
        <LabMap />
      </div>

      <aside className="lab-right">
        <div className="panel-section">
          <h3>Detections</h3>
          <DetectionFeed />
        </div>
        <div className="panel-section detail-section">
          <DetectionDetail />
        </div>
      </aside>

      {screening ? <ScreeningDialog onClose={() => setScreening(false)} /> : null}
    </div>
  );
}
