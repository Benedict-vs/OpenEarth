import { MapProvider } from "../../map/MapContext";
import { CatalogBrowser } from "./CatalogBrowser";
import { LayerEngine } from "./LayerEngine";
import { LayerPanel } from "./LayerPanel";

export function ExplorePage() {
  return (
    <MapProvider>
      <LayerEngine />
      <aside className="side-panel">
        <div className="panel-section">
          <h3>Catalog</h3>
          <CatalogBrowser />
        </div>
        <div className="panel-section">
          <h3>Layers</h3>
          <LayerPanel />
        </div>
      </aside>
    </MapProvider>
  );
}
