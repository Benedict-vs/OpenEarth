import { beforeEach, describe, expect, it } from "vitest";
import type { Site } from "../api/types";
import { useMethaneStore } from "./methaneStore";

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
});
