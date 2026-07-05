import { MapProvider } from "../../map/MapContext";
import { useInspector } from "../../map/useInspector";
import { useTerraDraw } from "../../map/useTerraDraw";
import { CatalogBrowser } from "./CatalogBrowser";
import { ChartPanel } from "./ChartPanel";
import { DateControl } from "./DateControl";
import { LayerEngine } from "./LayerEngine";
import { LayerPanel } from "./LayerPanel";
import { RoiToolbar } from "./RoiToolbar";

/** Inside MapProvider so its hooks can reach the map instance. */
function ExplorePanel() {
  const draw = useTerraDraw();
  const inspector = useInspector();
  return (
    <aside className="side-panel">
      <div className="panel-section">
        <h3>Region of interest</h3>
        <RoiToolbar draw={draw} />
      </div>
      <div className="panel-section">
        <h3>Dates</h3>
        <DateControl />
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
        <h3>Inspect</h3>
        <button
          className={inspector.active ? "inspect-toggle active" : "inspect-toggle"}
          onClick={inspector.toggle}
          title="Toggle, then click the map to read the top layer's pixel value"
        >
          {inspector.active ? "◎ Click the map to read a pixel" : "◎ Inspect pixel value"}
        </button>
      </div>
    </aside>
  );
}

export function ExplorePage() {
  return (
    <MapProvider south={<ChartPanel />}>
      <LayerEngine />
      <ExplorePanel />
    </MapProvider>
  );
}
