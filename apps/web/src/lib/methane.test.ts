import { describe, expect, it } from "vitest";
import type { DetectionDetail, Site } from "../api/types";
import {
  DEFAULT_ANALYSIS_KM,
  MIN_ANALYSIS_KM,
  analysisAreaPx,
  analysisAreaToBBox,
  defaultAnalysisArea,
  detectionNumbers,
  disagreementBadge,
  formatEmission,
  histogramOption,
  kghToTh,
  mlDetectionNumbers,
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
    expect(byLabel["Q (single-pass)"]).toBe("12.0 t/h");
    expect(byLabel["Candidates"]).toBe("3");
    expect(byLabel["ΔXCH4 max"]).toBe("200 ppb");
  });
});

describe("disagreementBadge", () => {
  it("maps agree / ml_only, null otherwise", () => {
    expect(disagreementBadge("agree")).toEqual({
      label: "Physics agrees",
      className: "disagreement agree",
    });
    expect(disagreementBadge("ml_only")?.className).toContain("ml-only");
    expect(disagreementBadge(undefined)).toBeNull();
    expect(disagreementBadge("nonsense")).toBeNull();
  });
});
