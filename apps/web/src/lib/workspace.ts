/**
 * Capture / restore the Explore view as a versioned workspace snapshot.
 *
 * These are the single translation point between the app's several stores and
 * the wire `WorkspaceState` (snake_case, `v`-tagged). `applyWorkspace` only
 * mutates stores — it never mints tiles or touches the map. Re-adding layers
 * through the store's `addLayer` lets the existing `useMintLayer` reaction do
 * the minting, so there is no second mint path to keep in sync.
 *
 * `captureWorkspace` writes v2 only (the window/period model); `applyWorkspace`
 * accepts both v1 and v2, migrating v1's mode/range/single shape on load.
 */
import type { WorkspaceState } from "../api/types";
import {
  defaultPeriod,
  defaultWindow,
  rangeToWindow,
  windowRange,
  type Period,
  type TimeWindow,
} from "./timeWindow";
import { useDateStore } from "../stores/dateStore";
import { useLayersStore } from "../stores/layersStore";
import { useRoiStore } from "../stores/roiStore";
import { useWindStore } from "../stores/windStore";

/** Snapshot the current view. Layer *identity + display state* only — mints are
 *  transient (tile URLs expire) and are re-derived on load. */
export function captureWorkspace(): WorkspaceState {
  const { layers } = useLayersStore.getState();
  const roi = useRoiStore.getState().roi;
  const { window, period } = useDateStore.getState();
  const wind = useWindStore.getState().enabled;

  return {
    v: 2,
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
      center: window.center,
      half_window_days: window.halfDays,
      period_start: period.start,
      period_end: period.end,
    },
    wind,
  };
}

/** Resolve a snapshot's date block to the window/period model (v1 → migrated). */
function migrateDate(state: WorkspaceState): { window: TimeWindow; period: Period } {
  const d = state.date;
  // v2: the shape is already window + period.
  if (state.v === 2 && d.center && d.period_start && d.period_end) {
    return {
      window: { center: d.center, halfDays: d.half_window_days },
      period: { start: d.period_start, end: d.period_end },
    };
  }
  // v1 "single": window = target_date ± half; period = center ± 180 d (end-clamped).
  if (d.mode === "single" && d.target_date) {
    return {
      window: { center: d.target_date, halfDays: d.half_window_days },
      period: windowRange({ center: d.target_date, halfDays: 180 }),
    };
  }
  // v1 "range": window = midpoint ± ceil(span/2); period = the range itself.
  if (d.start && d.end) {
    return {
      window: rangeToWindow(d.start, d.end),
      period: { start: d.start, end: d.end },
    };
  }
  // Malformed date block — fall back to session defaults rather than throw.
  return { window: defaultWindow(), period: defaultPeriod() };
}

/** Restore a snapshot: clear the layer stack, seed the shared stores, then
 *  re-add each layer (minting rides the existing reaction). Store-only — safe
 *  to call outside the map context. */
export function applyWorkspace(state: WorkspaceState): void {
  const layersStore = useLayersStore.getState();
  for (const layer of [...layersStore.layers]) layersStore.removeLayer(layer.id);

  useRoiStore.getState().setRoi(state.roi ?? null);
  const { window, period } = migrateDate(state);
  useDateStore.setState({ window, period });
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
