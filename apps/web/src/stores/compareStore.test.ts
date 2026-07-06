import { beforeEach, describe, expect, it } from "vitest";
import { useCompareStore } from "./compareStore";

function reset() {
  useCompareStore.setState({
    mode: "linked",
    orientation: "vertical",
    left: { dataset: "s2", product: "NDVI", viz: null, date: "2024-01-01" },
    right: { dataset: "s2", product: "NDVI", viz: null, date: "2024-06-01" },
  });
}

describe("compareStore", () => {
  beforeEach(reset);

  it("linked setShared fans product out to both sides, keeping dates", () => {
    useCompareStore.getState().setShared({ product: "NDWI" });
    const { left, right } = useCompareStore.getState();
    expect(left.product).toBe("NDWI");
    expect(right.product).toBe("NDWI");
    expect(left.date).toBe("2024-01-01"); // dates preserved
    expect(right.date).toBe("2024-06-01");
  });

  it("setSide patches only one side", () => {
    useCompareStore.getState().setSide("right", { date: "2024-12-31" });
    expect(useCompareStore.getState().right.date).toBe("2024-12-31");
    expect(useCompareStore.getState().left.date).toBe("2024-01-01");
  });

  it("switching mode preserves both side configs", () => {
    useCompareStore.getState().setSide("left", { product: "NDVI" });
    useCompareStore.getState().setSide("right", { product: "NDWI" });
    const before = { ...useCompareStore.getState() };
    useCompareStore.getState().setMode("independent");
    const after = useCompareStore.getState();
    expect(after.mode).toBe("independent");
    expect(after.left).toEqual(before.left);
    expect(after.right).toEqual(before.right);
  });

  it("independent setSide can diverge datasets per side", () => {
    useCompareStore.setState({ mode: "independent" });
    useCompareStore.getState().setSide("left", { dataset: "s5p", product: "NO2" });
    expect(useCompareStore.getState().left.dataset).toBe("s5p");
    expect(useCompareStore.getState().right.dataset).toBe("s2"); // untouched
  });
});
