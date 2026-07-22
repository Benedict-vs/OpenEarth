import { describe, expect, it } from "vitest";
import type { RenderDetail } from "../api/types";
import { plateInputFromDetail } from "./plate";

function detail(): RenderDetail {
  return {
    id: "abc",
    title: "Richmond Park",
    dataset: "s2",
    product: "RGB",
    status: "succeeded",
    frame_count: 3,
    fps: 6,
    format: "mp4",
    movie_bytes: 1000,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    draft: false,
    preset: "showcase",
    crops: [],
    roi: { kind: "bbox", west: -0.3, south: 51.42, east: -0.25, north: 51.46 },
    params: { dates: { start: "2024-01-01", end: "2024-12-31" } },
    manifest: {
      dataset: "s2",
      product: "RGB",
      width: 445,
      height: 300,
      vis: [0, 0.3],
      cancelled: false,
      composite: "clearest",
      post: { gap_fill: true },
      frames: [
        { index: 0, start: "2024-01-01", end: "2024-01-31", label: "Jan", status: "rendered", source: "s2", valid_fraction: 1, filled_fraction: 0 },
        { index: 1, start: "2024-02-01", end: "2024-02-29", label: "Feb", status: "rendered", source: "hls", valid_fraction: 0.8, filled_fraction: 0.1 },
        { index: null, start: "2024-03-01", end: "2024-03-31", label: "Mar", status: "empty", source: null, valid_fraction: null, filled_fraction: null },
      ],
    },
  } as unknown as RenderDetail;
}

describe("plateInputFromDetail", () => {
  it("extracts provenance for the chosen hero frame", () => {
    const input = plateInputFromDetail(detail(), 1, "/still/1");
    expect(input).not.toBeNull();
    expect(input!.title).toBe("Richmond Park");
    expect(input!.composite).toBe("clearest");
    expect(input!.frameLabel).toBe("Feb");
    expect(input!.source).toBe("hls");
    expect(input!.measured).toBe(0.8);
    expect(input!.borrowed).toBe(0.1);
    // ROI centre of the bbox
    expect(input!.centerLat).toBeCloseTo(51.44);
    expect(input!.centerLon).toBeCloseTo(-0.275);
    // Coverage across the whole render
    expect(input!.renderedCount).toBe(2);
    expect(input!.windowCount).toBe(3);
    expect(input!.blankCount).toBe(1);
    expect(input!.fallbackCount).toBe(1); // one frame stepped to hls
    expect(input!.start).toBe("2024-01-01");
  });

  it("returns null when the render has no manifest", () => {
    const d = { ...detail(), manifest: null } as unknown as RenderDetail;
    expect(plateInputFromDetail(d, 0, "/still/0")).toBeNull();
  });
});
