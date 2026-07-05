/**
 * Capture / restore the Explore view as a versioned workspace snapshot.
 *
 * These are the single translation point between the app's several stores and
 * the wire `WorkspaceState` (snake_case, `v`-tagged). `applyWorkspace` only
 * mutates stores — it never mints tiles or touches the map. Re-adding layers
 * through the store's `addLayer` lets the existing `useMintLayer` reaction do
 * the minting, so there is no second mint path to keep in sync.
 */
import type { WorkspaceState } from "../api/types";
import { useDateStore } from "../stores/dateStore";
import { useLayersStore } from "../stores/layersStore";
import { useRoiStore } from "../stores/roiStore";
import { useWindStore } from "../stores/windStore";

/** Snapshot the current view. Layer *identity + display state* only — mints are
 *  transient (tile URLs expire) and are re-derived on load. */
export function captureWorkspace(): WorkspaceState {
  const { layers } = useLayersStore.getState();
  const roi = useRoiStore.getState().roi;
  const { mode, start, end, targetDate, halfWindowDays } = useDateStore.getState();
  const wind = useWindStore.getState().enabled;

  return {
    v: 1,
    layers: layers.map((l) => ({
      dataset: l.dataset,
      product: l.product,
      label: l.label,
      opacity: l.opacity,
      visible: l.visible,
      viz_overrides: l.vizOverrides,
    })),
    roi: roi,
    date: {
      mode,
      start,
      end,
      target_date: targetDate,
      half_window_days: halfWindowDays,
    },
    wind,
  };
}

/** Restore a snapshot: clear the layer stack, seed the shared stores, then
 *  re-add each layer (minting rides the existing reaction). Store-only — safe
 *  to call outside the map context. */
export function applyWorkspace(state: WorkspaceState): void {
  const layersStore = useLayersStore.getState();
  for (const layer of [...layersStore.layers]) layersStore.removeLayer(layer.id);

  useRoiStore.getState().setRoi(state.roi ?? null);
  useDateStore.setState({
    mode: state.date.mode,
    start: state.date.start,
    end: state.date.end,
    targetDate: state.date.target_date,
    halfWindowDays: state.date.half_window_days,
  });
  useWindStore.getState().setEnabled(state.wind);

  for (const wl of state.layers) {
    const id = useLayersStore.getState().addLayer(wl.dataset, wl.product, wl.label);
    useLayersStore.setState((s) => ({
      layers: s.layers.map((l) =>
        l.id === id
          ? {
              ...l,
              opacity: wl.opacity,
              visible: wl.visible,
              vizOverrides: wl.viz_overrides ?? null,
            }
          : l,
      ),
    }));
  }
}
