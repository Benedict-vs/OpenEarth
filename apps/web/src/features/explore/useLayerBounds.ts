import { useMemo } from "react";
import { useCatalog } from "../../api/queries";
import { productBounds, unionBounds, type DateBounds } from "../../lib/coverage";
import { useLayersStore } from "../../stores/layersStore";

/**
 * Coverage for Explore's SHARED window/period: the span where at least one
 * layer has data. Union (not intersection) so adding an old archive never
 * locks the others out; a layer outside the window says so in its row.
 */
export function useLayerBounds(): DateBounds {
  const { data: catalog } = useCatalog();
  const layers = useLayersStore((s) => s.layers);
  return useMemo(
    () => unionBounds(layers.map((l) => productBounds(catalog, l.dataset, l.product))),
    [catalog, layers],
  );
}
