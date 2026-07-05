import { beforeEach, describe, expect, it } from "vitest";
import type { WorkspaceState } from "../api/types";
import { useDateStore } from "../stores/dateStore";
import { useLayersStore } from "../stores/layersStore";
import { useRoiStore } from "../stores/roiStore";
import { useWindStore } from "../stores/windStore";
import { applyWorkspace, captureWorkspace } from "./workspace";

const STATE: WorkspaceState = {
  v: 1,
  layers: [
    {
      dataset: "s2",
      product: "NDVI",
      label: "Sentinel-2 · NDVI",
      opacity: 0.6,
      visible: true,
      viz_overrides: null,
    },
    {
      dataset: "s5p",
      product: "CH4",
      label: "S5P · CH4",
      opacity: 0.3,
      visible: false,
      viz_overrides: { vis_min: 1800, vis_max: 1900 },
    },
  ],
  roi: { kind: "bbox", west: -103, south: 31.5, east: -102, north: 32.5 },
  date: {
    mode: "single",
    start: "2024-03-01",
    end: "2024-09-01",
    target_date: "2024-06-15",
    half_window_days: 5,
  },
  wind: true,
};

beforeEach(() => {
  useLayersStore.setState({ layers: [] });
  useRoiStore.setState({ roi: null, presetName: null });
  useWindStore.setState({ enabled: false });
  useDateStore.setState({
    mode: "range",
    start: "2024-01-01",
    end: "2024-01-31",
    targetDate: "2024-01-31",
    halfWindowDays: 3,
  });
});

describe("applyWorkspace", () => {
  it("clears the existing view and restores every store from the snapshot", () => {
    // Start dirty: a stray layer, a preset ROI, wind already on.
    useLayersStore.getState().addLayer("s1", "VV", "old layer");
    useRoiStore
      .getState()
      .applyPreset("Europe", { kind: "bbox", west: -25, south: 34, east: 45, north: 72 });
    useWindStore.setState({ enabled: true });

    applyWorkspace(STATE);

    const layers = useLayersStore.getState().layers;
    expect(layers).toHaveLength(2);
    expect(layers.map((l) => l.product)).toEqual(["NDVI", "CH4"]);
    expect(layers[0]!.opacity).toBe(0.6);
    expect(layers[0]!.visible).toBe(true);
    expect(layers[0]!.vizOverrides).toBeNull();
    expect(layers[1]!.visible).toBe(false);
    expect(layers[1]!.vizOverrides).toEqual({ vis_min: 1800, vis_max: 1900 });
    // Restored layers are pristine (no stale mint carried over).
    expect(layers[1]!.mint).toBeNull();
    expect(layers[1]!.status).toBe("idle");

    expect(useRoiStore.getState().roi).toEqual(STATE.roi);
    expect(useWindStore.getState().enabled).toBe(true);

    const d = useDateStore.getState();
    expect(d.mode).toBe("single");
    expect(d.start).toBe("2024-03-01");
    expect(d.targetDate).toBe("2024-06-15");
    expect(d.halfWindowDays).toBe(5);
  });

  it("restores an empty, whole-globe workspace", () => {
    useLayersStore.getState().addLayer("s2", "NDVI", "x");
    useRoiStore.getState().setRoi({ kind: "bbox", west: 8, south: 49, east: 9, north: 50 });

    applyWorkspace({ ...STATE, layers: [], roi: null, wind: false });

    expect(useLayersStore.getState().layers).toHaveLength(0);
    expect(useRoiStore.getState().roi).toBeNull();
    expect(useWindStore.getState().enabled).toBe(false);
  });
});

describe("captureWorkspace", () => {
  it("is the inverse of applyWorkspace (round-trips a snapshot)", () => {
    applyWorkspace(STATE);
    expect(captureWorkspace()).toEqual(STATE);
  });
});
