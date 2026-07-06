import { describe, expect, it } from "vitest";
import type { DetectionDetail } from "../api/types";
import {
  detectionNumbers,
  formatEmission,
  histogramOption,
  kghToTh,
  toImageCoordinates,
  verdictBadge,
} from "./methane";

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
