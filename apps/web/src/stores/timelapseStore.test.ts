import { beforeEach, describe, expect, it } from "vitest";
import { defaultForm, useTimelapseStore } from "./timelapseStore";

describe("timelapseStore", () => {
  beforeEach(() => {
    useTimelapseStore.setState({ form: defaultForm() });
  });

  it("merges partial form patches", () => {
    useTimelapseStore.getState().setForm({ productKey: "NDVI", fps: 12 });
    const { form } = useTimelapseStore.getState();
    expect(form.productKey).toBe("NDVI");
    expect(form.fps).toBe(12);
    expect(form.datasetId).toBe("s2"); // untouched fields survive
  });

  it("resets to defaults", () => {
    useTimelapseStore.getState().setForm({ productKey: "NDVI", format: "gif" });
    useTimelapseStore.getState().reset();
    expect(useTimelapseStore.getState().form.productKey).toBe("");
    expect(useTimelapseStore.getState().form.format).toBe("mp4");
  });

  it("defaults to a monthly ~1-year window", () => {
    const { start, end } = defaultForm();
    expect(defaultForm().stepMode).toBe("monthly");
    expect(new Date(end).getTime()).toBeGreaterThan(new Date(start).getTime());
  });
});
