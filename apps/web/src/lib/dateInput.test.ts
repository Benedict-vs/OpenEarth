import { describe, expect, it } from "vitest";
import { parseDateInput, shiftPeriod, stepDate } from "./dateInput";

describe("parseDateInput", () => {
  it("accepts ISO, unpadded, slashed, day-first and year-month forms", () => {
    expect(parseDateInput("2018-06-19")).toBe("2018-06-19");
    expect(parseDateInput(" 2018-6-9 ")).toBe("2018-06-09");
    expect(parseDateInput("2018/06/19")).toBe("2018-06-19");
    expect(parseDateInput("19.06.2018")).toBe("2018-06-19");
    expect(parseDateInput("2018-06")).toBe("2018-06-01");
  });

  it("rejects non-dates and impossible calendar days", () => {
    for (const bad of [
      "",
      "2018",
      "18-06-19",
      "2018-13-01",
      "2018-02-30",
      "2019-02-29",
      "abc",
      "0002-06-01",
    ]) {
      expect(parseDateInput(bad)).toBeNull();
    }
    expect(parseDateInput("2020-02-29")).toBe("2020-02-29"); // leap year
  });
});

describe("stepDate", () => {
  it("steps days across month and year boundaries", () => {
    expect(stepDate("2018-12-31", "day", 1)).toBe("2019-01-01");
    expect(stepDate("2018-03-01", "day", -1)).toBe("2018-02-28");
  });

  it("clamps month/year steps to the month's last day instead of overflowing", () => {
    expect(stepDate("2018-01-31", "month", 1)).toBe("2018-02-28");
    expect(stepDate("2020-02-29", "year", 1)).toBe("2021-02-28");
    expect(stepDate("2018-06-19", "year", -3)).toBe("2015-06-19");
    expect(stepDate("2018-01-15", "month", -1)).toBe("2017-12-15");
  });
});

describe("shiftPeriod", () => {
  it("moves by its own length or by a year", () => {
    const p = { start: "2018-06-10", end: "2018-07-05" };
    expect(shiftPeriod(p, "span", 1)).toEqual({ start: "2018-07-05", end: "2018-07-30" });
    expect(shiftPeriod(p, "span", -1)).toEqual({ start: "2018-05-16", end: "2018-06-10" });
    expect(shiftPeriod(p, "year", -1)).toEqual({ start: "2017-06-10", end: "2017-07-05" });
  });
});
