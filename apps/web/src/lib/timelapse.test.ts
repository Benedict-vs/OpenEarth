import { describe, expect, it } from "vitest";
import type { RoiIn } from "../api/types";
import { defaultForm } from "../stores/timelapseStore";
import {
  advanceIndex,
  buildTimelapseRequest,
  frameDurationMs,
  framesElapsed,
  preloadComplete,
} from "./timelapse";

describe("frame transport math", () => {
  it("derives per-frame duration from fps", () => {
    expect(frameDurationMs(10)).toBe(100);
    expect(frameDurationMs(4)).toBe(250);
    expect(frameDurationMs(0)).toBe(1000); // clamps to >= 1 fps
  });

  it("advances and wraps or clamps by loop mode", () => {
    expect(advanceIndex(0, 3, true)).toBe(1);
    expect(advanceIndex(2, 3, true)).toBe(0); // wrap
    expect(advanceIndex(2, 3, false)).toBe(2); // clamp at last frame
    expect(advanceIndex(0, 0, true)).toBe(0); // no frames
  });

  it("gates play on full preload", () => {
    expect(preloadComplete(0, 5)).toBe(false);
    expect(preloadComplete(4, 5)).toBe(false);
    expect(preloadComplete(5, 5)).toBe(true);
    expect(preloadComplete(0, 0)).toBe(false);
  });

  it("counts whole frames elapsed so a stall can skip ahead", () => {
    expect(framesElapsed(0, 6)).toBe(0);
    expect(framesElapsed(100, 10)).toBe(1);
    expect(framesElapsed(350, 10)).toBe(3);
  });
});

describe("buildTimelapseRequest", () => {
  const roi: RoiIn = { kind: "bbox", west: 8.5, south: 49.3, east: 8.8, north: 49.5 };

  it("maps the form to the API request shape", () => {
    const form = { ...defaultForm(), datasetId: "s2", productKey: "NDVI", title: "  " };
    const req = buildTimelapseRequest(form, roi);
    expect(req.dataset).toBe("s2");
    expect(req.product).toBe("NDVI");
    expect(req.roi).toEqual(roi);
    expect(req.title).toBeNull(); // blank title → null
    expect(req.step?.mode).toBe("monthly");
    expect(req.annotations?.date_label).toBe(true);
  });

  it("drops window_days outside interval mode", () => {
    const form = { ...defaultForm(), stepMode: "monthly" as const, windowDays: 30 };
    expect(buildTimelapseRequest(form, roi).step?.window_days).toBeNull();
  });

  it("clamps max_dim to the GIF ceiling for gif output", () => {
    const form = { ...defaultForm(), format: "gif" as const, maxDim: 1080 };
    expect(buildTimelapseRequest(form, roi).max_dim).toBe(720);
    const mp4 = { ...defaultForm(), format: "mp4" as const, maxDim: 1080 };
    expect(buildTimelapseRequest(mp4, roi).max_dim).toBe(1080);
  });

  it("carries the smoothing (tween) factor", () => {
    expect(buildTimelapseRequest({ ...defaultForm(), tween: 3 }, roi).tween).toBe(3);
    expect(buildTimelapseRequest(defaultForm(), roi).tween).toBe(0);
  });
});
