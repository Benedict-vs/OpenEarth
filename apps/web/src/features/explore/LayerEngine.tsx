/**
 * Headless per-layer controllers: each store layer gets one component whose
 * hooks mint tile URLs and bind the MapLibre source/layer. Keeping them as
 * components (not a loop of hooks) keeps React's hook rules satisfied as
 * layers come and go.
 */
import { useEffect } from "react";
import { useMapContext } from "../../map/MapContext";
import { useMintLayer } from "../../map/useMintLayer";
import { applyLayerOrder, useRasterLayer } from "../../map/useRasterLayer";
import { useTileRemint } from "../../map/useTileRemint";
import { useLayersStore, type Layer } from "../../stores/layersStore";

function LayerController({ layer }: { layer: Layer }) {
  useMintLayer(layer);
  useRasterLayer(layer);
  useTileRemint(layer);
  return null;
}

export function LayerEngine() {
  const layers = useLayersStore((state) => state.layers);
  const { map, ready } = useMapContext();

  // Re-assert z-order whenever the ordered id list (or any mint) changes —
  // a layer's map-side object only exists after its first mint.
  const orderKey = layers.map((l) => `${l.id}:${l.mint ? 1 : 0}`).join(",");
  useEffect(() => {
    if (!map || !ready) return;
    applyLayerOrder(
      map,
      layers.map((l) => l.id),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, ready, orderKey]);

  return (
    <>
      {layers.map((layer) => (
        <LayerController key={layer.id} layer={layer} />
      ))}
    </>
  );
}
