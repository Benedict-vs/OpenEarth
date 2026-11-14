import { beforeEach, describe, expect, it } from "vitest";
import type { Site } from "../api/types";
import { isStillOnSite, useMethaneStore } from "./methaneStore";

const SITE: Site = {
  id: 1,
  name: "Korpezhe",
  bbox: { kind: "bbox", west: 53.7, south: 38.2, east: 54.7, north: 38.8 },
  date_hint_start: "2018-06-01",
  date_hint_end: "2018-07-01",
  notes: null,
  created_at: "2024-01-01T00:00:00Z",
};

describe("methaneStore", () => {
  beforeEach(() => {
    useMethaneStore.setState({
      selectedSite: null,
      dates: { start: "2024-06-01", end: "2024-09-01" },
      targetSceneId: null,
      referenceSceneId: "auto",
      referenceSceneTime: null,
      params: {
        method: "mbmp",
        referenceMode: "single",
        kSigma: 2,
        minAreaPx: 5,
        seed: 0,
      },
      selectedDetectionId: null,
      job: null,
    });
  });

  it("selecting a site seeds dates from its hint and resets selection", () => {
    const store = useMethaneStore.getState();
    store.setTarget("some-scene");
    store.selectDetection("det-1");
    store.selectSite(SITE);

    const next = useMethaneStore.getState();
    expect(next.selectedSite?.id).toBe(1);
    expect(next.dates).toEqual({ start: "2018-06-01", end: "2018-07-01" });
    expect(next.targetSceneId).toBeNull();
    expect(next.selectedDetectionId).toBeNull();
    expect(next.referenceSceneId).toBe("auto");
  });

  it("selecting a site seeds a centred analysis area and disarms placing", () => {
    useMethaneStore.setState({ placingArea: true });
    useMethaneStore.getState().selectSite(SITE);

    const next = useMethaneStore.getState();
    expect(next.analysisArea?.lon).toBeCloseTo(54.2, 9);
    expect(next.analysisArea?.lat).toBeCloseTo(38.5, 9);
    expect(next.analysisArea?.sizeKm).toBe(10);
    expect(next.placingArea).toBe(false);

    next.setAnalysisArea({ lon: 54.0, lat: 38.4 });
    expect(useMethaneStore.getState().analysisArea).toEqual({ lon: 54.0, lat: 38.4, sizeKm: 10 });
  });

  it("setTarget carries the scene time; clearing the target clears it", () => {
    useMethaneStore.getState().setTarget("scene-1", "2024-06-07T07:06:29Z");
    expect(useMethaneStore.getState().targetSceneTime).toBe("2024-06-07T07:06:29Z");
    useMethaneStore.getState().setTarget(null);
    expect(useMethaneStore.getState().targetSceneTime).toBeNull();
  });

  it("run lifecycle: job progress transitions", () => {
    const store = useMethaneStore.getState();
    store.setJob({ jobId: "j1", step: 3, total: 7, message: "Sampling wind", status: "running" });
    expect(useMethaneStore.getState().job?.step).toBe(3);
    store.setJob({ jobId: "j1", step: 7, total: 7, message: "Done", status: "done" });
    expect(useMethaneStore.getState().job?.status).toBe("done");
  });

  it("setParams merges partial run params", () => {
    useMethaneStore.getState().setParams({ kSigma: 2.5 });
    useMethaneStore.getState().setParams({ method: "mbsp" });
    const params = useMethaneStore.getState().params;
    expect(params.kSigma).toBe(2.5);
    expect(params.method).toBe("mbsp");
  });

  it("pinning a reference forces single mode; composite clears the pin", () => {
    const store = useMethaneStore.getState();
    store.setParams({ referenceMode: "composite" });
    store.setReference("ref-1", "2018-06-24T07:13:59Z");

    let next = useMethaneStore.getState();
    expect(next.referenceSceneId).toBe("ref-1");
    expect(next.referenceSceneTime).toBe("2018-06-24T07:13:59Z");
    expect(next.params.referenceMode).toBe("single");

    next.setParams({ referenceMode: "composite" });
    next = useMethaneStore.getState();
    expect(next.referenceSceneId).toBe("auto");
    expect(next.referenceSceneTime).toBeNull();

    // Unrelated param changes leave a pin alone.
    next.setReference("ref-1", "2018-06-24T07:13:59Z");
    next.setParams({ kSigma: 2.5 });
    expect(useMethaneStore.getState().referenceSceneId).toBe("ref-1");
  });

  it("promoting the pinned reference to target unpins it", () => {
    const store = useMethaneStore.getState();
    store.setReference("scene-2", "2018-06-24T07:13:59Z");
    store.setTarget("scene-1", "2018-06-19T07:12:20Z");
    expect(useMethaneStore.getState().referenceSceneId).toBe("scene-2");

    store.setTarget("scene-2", "2018-06-24T07:13:59Z");
    expect(useMethaneStore.getState().referenceSceneId).toBe("auto");
    expect(useMethaneStore.getState().referenceSceneTime).toBeNull();
  });

  it("isStillOnSite guards late job callbacks after a site switch", () => {
    useMethaneStore.getState().selectSite(SITE);
    expect(isStillOnSite(1)).toBe(true);
    useMethaneStore.getState().selectSite({ ...SITE, id: 2, name: "Other" });
    expect(isStillOnSite(1)).toBe(false);
    useMethaneStore.getState().selectSite(null);
    expect(isStillOnSite(1)).toBe(false);
  });
});
