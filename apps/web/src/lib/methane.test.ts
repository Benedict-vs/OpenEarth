import { describe, expect, it } from "vitest";
import { ApiError } from "../api/client";
import type { DetectionDetail, Site } from "../api/types";
import {
  DEFAULT_ANALYSIS_KM,
  FLAG_HINTS,
  MIN_ANALYSIS_KM,
  analysisAreaPx,
  analysisAreaToBBox,
  describeApiError,
  defaultAnalysisArea,
  detectionNumbers,
  disagreementBadge,
  formatEmission,
  formatFloorTh,
  histogramOption,
  isIntInRange,
  kghToTh,
  mgrsTile,
  mlDetectionNumbers,
  parseNumberInput,
  pctFraction,
  referenceCheck,
  referenceDeltaDays,
  toImageCoordinates,
  verdictBadge,
} from "./methane";

function siteWith(bbox: { west: number; south: number; east: number; north: number }): Site {
  return { id: 1, name: "t", bbox: { kind: "bbox", ...bbox } } as Site;
}

describe("analysis area", () => {
  it("builds a square bbox widened by 1/cos(lat)", () => {
    const box = analysisAreaToBBox({ lon: 54.2, lat: 38.5, sizeKm: 10 });
    const dLat = box.north - box.south;
    const dLon = box.east - box.west;
    expect(dLat).toBeCloseTo(10 / 111.32, 6);
    expect(dLon).toBeCloseTo(10 / (111.32 * Math.cos((38.5 * Math.PI) / 180)), 6);
    expect((box.west + box.east) / 2).toBeCloseTo(54.2, 9);
    expect((box.south + box.north) / 2).toBeCloseTo(38.5, 9);
  });

  it("stays under the 1024 px chip limit at the max size", () => {
    expect(analysisAreaPx(20)).toBeLessThanOrEqual(1024);
    expect(analysisAreaPx(10)).toBe(500);
  });

  it("defaults to a 10 km box centred on the site", () => {
    const area = defaultAnalysisArea(
      siteWith({ west: 53.7, south: 38.2, east: 54.7, north: 38.8 }),
    );
    expect(area.lon).toBeCloseTo(54.2, 9);
    expect(area.lat).toBeCloseTo(38.5, 9);
    expect(area.sizeKm).toBe(DEFAULT_ANALYSIS_KM);
  });

  it("shrinks the default to fit a small site, never below the minimum", () => {
    const small = defaultAnalysisArea(siteWith({ west: 0, south: 0, east: 0.05, north: 0.05 }));
    expect(small.sizeKm).toBeLessThan(DEFAULT_ANALYSIS_KM);
    expect(small.sizeKm).toBeGreaterThanOrEqual(MIN_ANALYSIS_KM);
  });
});

describe("toImageCoordinates", () => {
  it("passes a valid 4-corner bounds through", () => {
    const bounds = [
      [53.9, 38.5],
      [54.0, 38.5],
      [54.0, 38.4],
      [53.9, 38.4],
    ];
    expect(toImageCoordinates(bounds)).toEqual(bounds);
  });

  it("returns null for missing or malformed bounds", () => {
    expect(toImageCoordinates(null)).toBeNull();
    expect(toImageCoordinates([[1, 2]])).toBeNull();
  });
});

describe("formatEmission", () => {
  it("converts kg/h to t/h with ± σ", () => {
    expect(formatEmission(11200, 5200)).toBe("11.2 ± 5.2 t/h");
  });
  it("handles a missing σ and a missing Q", () => {
    expect(formatEmission(8000, null)).toBe("8.0 t/h");
    expect(formatEmission(null, null)).toBe("—");
  });
  it("kghToTh divides by 1000", () => {
    expect(kghToTh(2500)).toBe(2.5);
    expect(kghToTh(null)).toBeNull();
  });
});

describe("verdictBadge", () => {
  it("maps verdicts to labels + classes", () => {
    expect(verdictBadge("confirmed")).toEqual({
      label: "Confirmed",
      className: "verdict confirmed",
    });
    expect(verdictBadge("plausible").className).toContain("plausible");
    expect(verdictBadge(undefined).label).toBe("Not validated");
  });
});

describe("histogramOption", () => {
  it("builds bin centres in t/h and keeps counts", () => {
    const option = histogramOption({ edges: [0, 2000, 4000], counts: [3, 7] }) as {
      xAxis: { data: string[] };
      series: { data: number[] }[];
    };
    expect(option.xAxis.data).toEqual(["1.0", "3.0"]);
    expect(option.series[0]!.data).toEqual([3, 7]);
  });
  it("tolerates an undefined histogram", () => {
    const option = histogramOption(undefined) as { series: { data: number[] }[] };
    expect(option.series[0]!.data).toEqual([]);
  });
});

describe("detectionNumbers", () => {
  it("formats the numbers table, NaN/undefined → —", () => {
    const detail = {
      q_kg_h: 8000,
      q_sigma_kg_h: 2000,
      ime_kg: 1000,
      wind_from_deg: 270,
      xch4_max_ppb: 120,
      result: {
        l_m: 80,
        u_eff_ms: 1.77,
        u10_ms: 4,
        sigma_u10_ms: 1.5,
        calibration: { c_target: 1.02, c_ref: null },
      },
    } as unknown as DetectionDetail;
    const rows = detectionNumbers(detail);
    const byLabel = Object.fromEntries(rows.map((r) => [r.label, r.value]));
    expect(byLabel["Q (median)"]).toBe("8.0 ± 2.0 t/h");
    expect(byLabel["Plume length L"]).toBe("80 m");
    expect(byLabel["c (reference)"]).toBe("—");
  });
});

describe("mlDetectionNumbers", () => {
  it("shows single-pass Q (no ± σ) and candidate count", () => {
    const detail = {
      q_kg_h: 12000,
      q_sigma_kg_h: null,
      ime_kg: 1500,
      u10_ms: 3.2,
      wind_from_deg: 90,
      xch4_max_ppb: 200,
      result: { n_candidates: 3 },
    } as unknown as DetectionDetail;
    const byLabel = Object.fromEntries(mlDetectionNumbers(detail).map((r) => [r.label, r.value]));
    expect(byLabel["Q (single-pass)"]).toBe("~12.0 t/h"); // point estimate → tilde (fix 9a)
    expect(byLabel["Candidates"]).toBe("3");
    expect(byLabel["ΔXCH4 max"]).toBe("200 ppb");
  });
});

describe("formatEmission approx", () => {
  it("prefixes ~ only when approx is set; physics unchanged", () => {
    expect(formatEmission(4800, null, { approx: true })).toBe("~4.8 t/h");
    expect(formatEmission(4800, null)).toBe("4.8 t/h");
    expect(formatEmission(8000, 2000)).toBe("8.0 ± 2.0 t/h");
    expect(formatEmission(null, null, { approx: true })).toBe("—");
  });
});

describe("formatFloorTh", () => {
  it("formats a kg/h floor as t/h, else —", () => {
    expect(formatFloorTh(6300)).toBe("6.3 t/h");
    expect(formatFloorTh(null)).toBe("—");
    expect(formatFloorTh(undefined)).toBe("—");
  });
});

describe("pctFraction", () => {
  it("renders a 0–1 fraction as integer percent, else —", () => {
    expect(pctFraction(0.6701)).toBe("67%");
    expect(pctFraction(0)).toBe("0%");
    expect(pctFraction(null)).toBe("—");
    expect(pctFraction(undefined)).toBe("—");
    expect(pctFraction(NaN)).toBe("—");
  });
});

describe("disagreementBadge", () => {
  it("maps the three read-derived agreement states, null otherwise (fix 8)", () => {
    expect(disagreementBadge("agree")).toEqual({
      label: "Physics agrees (plume found)",
      className: "disagreement agree",
    });
    expect(disagreementBadge("physics_no_plume")).toEqual({
      label: "Physics found no plume",
      className: "disagreement no-plume",
    });
    expect(disagreementBadge("physics_not_run")).toEqual({
      label: "Physics not run",
      className: "disagreement not-run",
    });
    expect(disagreementBadge(undefined)).toBeNull();
    expect(disagreementBadge("nonsense")).toBeNull();
  });
});

describe("referenceCheck", () => {
  // Korpezhe 2018-06-19 with its documented pinned reference (validate_events.py).
  const TARGET = {
    scene_id: "20180619T070619_20180619T071220_T39SYC",
    time: "2018-06-19T07:12:20Z",
    cloud_pct: 0,
    relative_orbit: 106,
    spacecraft: "Sentinel-2A",
  };
  const scene = (over: Partial<typeof TARGET>) => ({ ...TARGET, ...over });

  it("accepts the documented Korpezhe reference with no warnings", () => {
    const ref = scene({
      scene_id: "20180624T070621_20180624T071359_T39SYC",
      time: "2018-06-24T07:13:59Z",
    });
    expect(referenceCheck(TARGET, ref)).toEqual({ ok: true, reason: null, warnings: [] });
    expect(referenceDeltaDays(TARGET.time, ref.time)).toBeCloseTo(5, 1);
  });

  it("refuses the target itself and a same-overpass adjacent tile", () => {
    expect(referenceCheck(TARGET, TARGET).ok).toBe(false);
    const adjacent = scene({
      scene_id: "20180619T070619_20180619T071220_T39SYD",
      time: "2018-06-19T07:12:24Z",
    });
    const check = referenceCheck(TARGET, adjacent);
    expect(check.ok).toBe(false);
    expect(check.reason).toMatch(/same overpass/i);
  });

  it("mirrors the server's ±130 d window on the target's UTC day (exclusive end)", () => {
    // Window = [2018-02-09, 2018-10-27) from target day 2018-06-19.
    expect(referenceCheck(TARGET, scene({ scene_id: "a", time: "2018-02-09T00:00:00Z" })).ok).toBe(
      true,
    );
    expect(referenceCheck(TARGET, scene({ scene_id: "b", time: "2018-02-08T23:59:59Z" })).ok).toBe(
      false,
    );
    expect(referenceCheck(TARGET, scene({ scene_id: "c", time: "2018-10-26T23:59:59Z" })).ok).toBe(
      true,
    );
    expect(referenceCheck(TARGET, scene({ scene_id: "d", time: "2018-10-27T00:00:00Z" })).ok).toBe(
      false,
    );
  });

  it("warns (but allows) different orbit, spacecraft, tile and cloudy scenes", () => {
    const check = referenceCheck(
      TARGET,
      scene({
        scene_id: "20180629T065621_20180629T070000_T40SBH",
        time: "2018-06-29T07:00:00Z",
        relative_orbit: 63,
        spacecraft: "Sentinel-2B",
        cloud_pct: 42,
      }),
    );
    expect(check.ok).toBe(true);
    expect(check.warnings).toHaveLength(4);
    expect(check.warnings.join(" ")).toMatch(/different_orbit_reference/);
    expect(check.warnings.join(" ")).toMatch(/cross_tile_reference/);
  });
});

describe("mgrsTile", () => {
  it("parses the tile suffix and rejects other ids", () => {
    expect(mgrsTile("20180619T070619_20180619T071220_T39SYC")).toBe("39SYC");
    expect(mgrsTile("not-a-scene")).toBeNull();
  });
});

describe("number inputs", () => {
  it("parses blank as NaN, never 0", () => {
    expect(parseNumberInput("")).toBeNaN();
    expect(parseNumberInput("  ")).toBeNaN();
    expect(parseNumberInput("abc")).toBeNaN();
    expect(parseNumberInput("0")).toBe(0);
    expect(parseNumberInput("-103.85")).toBe(-103.85);
  });

  it("validates whole numbers in range", () => {
    expect(isIntInRange(5, 1)).toBe(true);
    expect(isIntInRange(0, 1)).toBe(false);
    expect(isIntInRange(Number.NaN, 0)).toBe(false);
    expect(isIntInRange(2.5, 0)).toBe(false);
    expect(isIntInRange(201, 1, 200)).toBe(false);
  });
});

describe("describeApiError", () => {
  it("turns a FastAPI 422 list into field: message", () => {
    const err = new ApiError(
      422,
      JSON.stringify([
        {
          type: "greater_than_equal",
          loc: ["body", "min_area_px"],
          msg: "Input should be greater than or equal to 1",
        },
      ]),
    );
    expect(describeApiError(err)).toBe("min_area_px: Input should be greater than or equal to 1");
  });

  it("passes plain details and generic errors through", () => {
    expect(describeApiError(new ApiError(409, "A site with that name already exists."))).toBe(
      "A site with that name already exists.",
    );
    expect(describeApiError(new Error("network down"))).toBe("network down");
  });
});

it("explains the MBMP→MBSP fallback flag", () => {
  expect(FLAG_HINTS.no_reference_mbsp_fallback).toMatch(/single-scene MBSP/);
});
