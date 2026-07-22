import { describe, expect, it } from "vitest";
import type { Preflight, RoiIn } from "../api/types";
import { defaultForm } from "../stores/timelapseStore";
import { PRESETS } from "./presets";
import {
  advanceIndex,
  buildTimelapseRequest,
  compileCloud,
  compileGrade,
  frameDurationMs,
  framesElapsed,
  middleWindow,
  pacingSummary,
  planFps,
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

  it("a fresh form compiles to the legacy recipe (back-compat)", () => {
    const req = buildTimelapseRequest(defaultForm(), roi);
    expect(req.composite).toBe("mean");
    expect(req.gap_fill).toBe(false);
    expect(req.deflicker).toBe(false);
    expect(req.fallback_source).toBe(false);
    expect(req.cloud_display).toBe("composite");
    expect(req.grade).toBeNull();
    expect(req.preset).toBeNull();
    expect(req.fps).toBe(6); // frame-first: fps present
  });

  it("expands a preset into its knobs + provenance label", () => {
    const showcase = PRESETS.find((p) => p.id === "showcase")!;
    const req = buildTimelapseRequest({ ...defaultForm(), ...showcase.patch }, roi);
    expect(req.preset).toBe("showcase");
    expect(req.composite).toBe("clearest");
    expect(req.gap_fill).toBe(true);
    expect(req.deflicker).toBe(true);
    expect(req.fallback_source).toBe(true);
    expect(req.grade?.curve).toBe("cinematic");
  });

  it("duration-first omits fps and sends duration_s (XOR)", () => {
    const req = buildTimelapseRequest({ ...defaultForm(), authoringMode: "duration", durationS: 10 }, roi);
    expect("fps" in req).toBe(false);
    expect(req.duration_s).toBe(10);
  });

  it("threads the draft flag", () => {
    expect(buildTimelapseRequest(defaultForm(), roi, { draft: true }).draft).toBe(true);
    expect(buildTimelapseRequest(defaultForm(), roi).draft).toBe(false);
  });
});

describe("cloud + grade compilation", () => {
  it("maps cloud modes to the two API fields", () => {
    expect(compileCloud({ ...defaultForm(), cloudMode: "fill" })).toEqual({
      gap_fill: true,
      cloud_display: "composite",
    });
    expect(compileCloud({ ...defaultForm(), cloudMode: "show" })).toEqual({
      gap_fill: false,
      cloud_display: "composite",
    });
    const tint = compileCloud({ ...defaultForm(), cloudMode: "tint", tintColor: "#abcdef" });
    expect(tint).toEqual({ gap_fill: false, cloud_display: "tint:#abcdef" });
  });

  it("returns null for the identity grade, an object otherwise", () => {
    expect(compileGrade(defaultForm())).toBeNull();
    expect(compileGrade({ ...defaultForm(), gradeCurve: "vivid" })?.curve).toBe("vivid");
    expect(compileGrade({ ...defaultForm(), gradeSaturation: 1.2 })?.saturation).toBe(1.2);
  });
});

describe("pacing (mirrors plan_fps)", () => {
  it("frame-first clamps fps to [1, 30]", () => {
    const base = defaultForm();
    expect(planFps(24, { ...base, authoringMode: "fps", fps: 12 })).toBe(12);
    expect(planFps(24, { ...base, authoringMode: "fps", fps: 99 })).toBe(30);
  });

  it("duration-first fits the frames into the target seconds", () => {
    const base = { ...defaultForm(), authoringMode: "duration" as const };
    expect(planFps(48, { ...base, durationS: 4 })).toBe(12); // 48 / 4
    expect(planFps(10, { ...base, durationS: 20 })).toBe(1); // clamps up to 1
  });

  it("summarises the pacing math for the UI", () => {
    const preflight: Preflight = {
      frame_count: 11,
      empty_count: 1,
      native_max_dim: 445,
      windows: Array.from({ length: 12 }, (_, i) => ({
        start: "2024-01-01",
        end: "2024-01-31",
        label: `M${i}`,
        scene_count: i === 5 ? 0 : 3,
        mean_cloud: null,
        source: "s2",
      })),
    };
    const s = pacingSummary(preflight, { ...defaultForm(), authoringMode: "fps", fps: 12 });
    expect(s.windows).toBe(12);
    expect(s.frames).toBe(11);
    expect(s.fps).toBe(12);
    expect(s.label).toContain("12 windows → 11 frames @ 12 fps");
  });

  it("picks the middle window with data for a preview", () => {
    const preflight: Preflight = {
      frame_count: 3,
      empty_count: 0,
      native_max_dim: 445,
      windows: [
        { start: "2024-01-01", end: "2024-01-31", label: "Jan", scene_count: 2, mean_cloud: null, source: "s2" },
        { start: "2024-02-01", end: "2024-02-28", label: "Feb", scene_count: 4, mean_cloud: null, source: "s2" },
        { start: "2024-03-01", end: "2024-03-31", label: "Mar", scene_count: 1, mean_cloud: null, source: "s2" },
      ],
    };
    expect(middleWindow(preflight)).toEqual({ start: "2024-02-01", end: "2024-02-28" });
  });
});
