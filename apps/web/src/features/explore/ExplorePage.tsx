import { MapProvider } from "../../map/MapContext";
import { useInspector } from "../../map/useInspector";
import { useTerraDraw, type DrawApi } from "../../map/useTerraDraw";
import { WindOverlay } from "../../map/WindOverlay";
import { WindParticles } from "../../map/WindParticles";
import { useDateStore } from "../../stores/dateStore";
import { useWindStore } from "../../stores/windStore";
import { AnimationBar } from "./AnimationBar";
import { CatalogBrowser } from "./CatalogBrowser";
import { ChartPanel } from "./ChartPanel";
import { LayerEngine } from "./LayerEngine";
import { LayerPanel } from "./LayerPanel";
import { PlaybackBar } from "../timelapse/PlaybackBar";
import { RoiToolbar } from "./RoiToolbar";
import { TimeWindowPicker } from "./TimeWindowPicker";

/** Inside MapProvider so its hooks can reach the map instance. */
function ExplorePanel({ draw }: { draw: DrawApi }) {
  const inspector = useInspector();
  return (
    <aside className="side-panel">
      <div className="panel-section">
        <h3>Region of interest</h3>
        <RoiToolbar draw={draw} />
      </div>
      <div className="panel-section">
        <h3>Window</h3>
        <WindowControl />
      </div>
      <div className="panel-section">
        <h3>Catalog</h3>
        <CatalogBrowser />
      </div>
      <div className="panel-section">
        <h3>Layers</h3>
        <LayerPanel />
      </div>
      <div className="panel-section">
        <h3>Animate</h3>
        <AnimationBar />
      </div>
      <div className="panel-section">
        <h3>Inspect</h3>
        <button
          className={inspector.active ? "inspect-toggle active" : "inspect-toggle"}
          onClick={inspector.toggle}
          title="Toggle, then click the map to read the top layer's pixel value"
        >
          {inspector.active ? "◎ Click the map to read a pixel" : "◎ Inspect pixel value"}
        </button>
      </div>
      <div className="panel-section">
        <h3>Wind</h3>
        <WindToggle />
      </div>
    </aside>
  );
}

/** The Explore sidebar's window control, wired to the shared dateStore. */
function WindowControl() {
  const window = useDateStore((s) => s.window);
  const setWindow = useDateStore((s) => s.setWindow);
  return <TimeWindowPicker window={window} onChange={setWindow} />;
}

function WindToggle() {
  const enabled = useWindStore((s) => s.enabled);
  const toggle = useWindStore((s) => s.toggle);
  const particles = useWindStore((s) => s.particlesEnabled);
  const toggleParticles = useWindStore((s) => s.toggleParticles);
  return (
    <>
      <button
        className={enabled ? "inspect-toggle active" : "inspect-toggle"}
        onClick={toggle}
        title="Overlay ERA5 10 m wind arrows for the active date over the map view"
      >
        {enabled ? "◈ Wind overlay on" : "◈ Show wind overlay"}
      </button>
      <button
        className={particles ? "inspect-toggle active" : "inspect-toggle"}
        onClick={toggleParticles}
        title="Animated GPU wind particles advecting along the same ERA5 field"
      >
        {particles ? "✳ Wind particles on" : "✳ Show wind particles"}
      </button>
      <p className="muted wind-note">
        ERA5 10 m wind at 12:00 UTC on the active date — weather context, not overpass-matched.
      </p>
    </>
  );
}

/** Owns the draw api so LayerEngine can drop the ROI outline below the data
 *  rasters whenever the user is not actively drawing. */
function ExploreInner() {
  const draw = useTerraDraw();
  return (
    <>
      <LayerEngine drawActive={draw.mode !== "static"} />
      <ExplorePanel draw={draw} />
    </>
  );
}

export function ExplorePage() {
  return (
    <MapProvider south={<ChartPanel />}>
      <ExploreInner />
      <WindOverlay />
      <WindParticles />
      <PlaybackBar />
    </MapProvider>
  );
}
