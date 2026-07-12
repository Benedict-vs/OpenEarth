import { beforeEach, describe, expect, it } from "vitest";
import type { WorkspaceState } from "../api/types";
import { defaultPeriod, defaultWindow } from "./timeWindow";
import { useDateStore } from "../stores/dateStore";
import { useLayersStore } from "../stores/layersStore";
import { useRoiStore } from "../stores/roiStore";
import { useWindStore } from "../stores/windStore";
import { applyWorkspace, captureWorkspace } from "./workspace";

const LAYERS: WorkspaceState["layers"] = [
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
];

const ROI: WorkspaceState["roi"] = { kind: "bbox", west: -103, south: 31.5, east: -102, north: 32.5 };

// A committed v2 snapshot (the shape captureWorkspace writes).
const STATE_V2: WorkspaceState = {
  v: 2,
  layers: LAYERS,
  roi: ROI,
  date: {
    center: "2024-06-15",
    half_window_days: 15,
    period_start: "2024-01-01",
    period_end: "2024-12-31",
  },
  wind: true,
};

// A committed v1 "single" snapshot — kept so the migration path stays exercised
// after v1 stops being writable.
const STATE_V1_SINGLE: WorkspaceState = {
  v: 1,
  layers: LAYERS,
  roi: ROI,
  date: {
    mode: "single",
    start: "2024-03-01",
    end: "2024-09-01",
    target_date: "2024-06-15",
    half_window_days: 5,
  },
  wind: true,
};

// A committed v1 "range" snapshot.
const STATE_V1_RANGE: WorkspaceState = {
  v: 1,
  layers: [],
  roi: null,
  date: {
    mode: "range",
    start: "2024-05-28",
    end: "2024-06-27",
    target_date: "2024-06-27",
    half_window_days: 3,
  },
  wind: false,
};

beforeEach(() => {
  useLayersStore.setState({ layers: [] });
  useRoiStore.setState({ roi: null, presetName: null });
  useWindStore.setState({ enabled: false });
  useDateStore.setState({ window: defaultWindow(), period: defaultPeriod() });
});

describe("applyWorkspace", () => {
  it("restores a v2 snapshot directly (window + period)", () => {
    // Start dirty: a stray layer, a preset ROI, wind already on.
    useLayersStore.getState().addLayer("s1", "VV", "old layer");
    useWindStore.setState({ enabled: true });

    applyWorkspace(STATE_V2);

    const layers = useLayersStore.getState().layers;
    expect(layers).toHaveLength(2);
    expect(layers.map((l) => l.product)).toEqual(["NDVI", "CH4"]);
    expect(layers[0]!.opacity).toBe(0.6);
    expect(layers[1]!.visible).toBe(false);
    expect(layers[1]!.vizOverrides).toEqual({ vis_min: 1800, vis_max: 1900 });
    // Restored layers are pristine (no stale mint carried over).
    expect(layers[1]!.mint).toBeNull();
    expect(layers[1]!.status).toBe("idle");

    expect(useRoiStore.getState().roi).toEqual(ROI);
    expect(useWindStore.getState().enabled).toBe(true);

    const d = useDateStore.getState();
    expect(d.window).toEqual({ center: "2024-06-15", halfDays: 15 });
    expect(d.period).toEqual({ start: "2024-01-01", end: "2024-12-31" });
  });

  it("migrates a v1 'single' snapshot (window from target±half, period from center±180 d)", () => {
    applyWorkspace(STATE_V1_SINGLE);
    const d = useDateStore.getState();
    expect(d.window).toEqual({ center: "2024-06-15", halfDays: 5 });
    // center ± 180 d, end-clamped to today (2024-12-12 is far in the past).
    expect(d.period).toEqual({ start: "2023-12-18", end: "2024-12-12" });
  });

  it("migrates a v1 'range' snapshot (window from the range midpoint, period = range)", () => {
    applyWorkspace(STATE_V1_RANGE);
    const d = useDateStore.getState();
    // 2024-05-28 → 2024-06-27 is 30 days: midpoint 2024-06-12, ±15.
    expect(d.window).toEqual({ center: "2024-06-12", halfDays: 15 });
    expect(d.period).toEqual({ start: "2024-05-28", end: "2024-06-27" });
  });

  it("restores an empty, whole-globe workspace", () => {
    useLayersStore.getState().addLayer("s2", "NDVI", "x");
    useRoiStore.getState().setRoi({ kind: "bbox", west: 8, south: 49, east: 9, north: 50 });

    applyWorkspace({ ...STATE_V2, layers: [], roi: null, wind: false });

    expect(useLayersStore.getState().layers).toHaveLength(0);
    expect(useRoiStore.getState().roi).toBeNull();
    expect(useWindStore.getState().enabled).toBe(false);
  });
});

describe("captureWorkspace", () => {
  it("writes v2 and round-trips through applyWorkspace", () => {
    applyWorkspace(STATE_V2);
    const captured = captureWorkspace();
    expect(captured.v).toBe(2);
    expect(captured).toEqual(STATE_V2);
  });
});
