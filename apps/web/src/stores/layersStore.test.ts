import { beforeEach, describe, expect, it } from "vitest";
import type { Legend } from "../api/types";
import { useLayersStore, type LayerMint } from "./layersStore";

const LEGEND: Legend = {
  min: 0,
  max: 1,
  unit: "u",
  palette: ["#000000", "#ffffff"],
  display_scale: 1,
  is_rgb: false,
  description: "",
};

function mint(url: string): LayerMint {
  return {
    tileUrl: url,
    mintedAt: 1000,
    expiresAt: 2000,
    attribution: "test",
    legend: LEGEND,
    paramsKey: '{"dataset":"s2"}',
  };
}

describe("layersStore", () => {
  beforeEach(() => {
    useLayersStore.setState({ layers: [] });
  });

  it("adds layers bottom-to-top with sane defaults", () => {
    const { addLayer } = useLayersStore.getState();
    const a = addLayer("s2", "NDVI", "S2 · NDVI");
    const b = addLayer("s5p", "NO2", "S5P · NO2");
    const layers = useLayersStore.getState().layers;
    expect(layers.map((l) => l.id)).toEqual([a, b]);
    expect(layers[0]).toMatchObject({
      dataset: "s2",
      product: "NDVI",
      visible: true,
      status: "idle",
      mint: null,
    });
    expect(layers[0]!.opacity).toBeGreaterThan(0);
  });

  it("removes a layer by id", () => {
    const { addLayer, removeLayer } = useLayersStore.getState();
    const a = addLayer("s2", "NDVI", "a");
    const b = addLayer("s2", "NDWI", "b");
    removeLayer(a);
    expect(useLayersStore.getState().layers.map((l) => l.id)).toEqual([b]);
  });

  it("moves layers up and down, clamped at the ends", () => {
    const { addLayer, moveLayer } = useLayersStore.getState();
    const a = addLayer("s2", "NDVI", "a");
    const b = addLayer("s2", "NDWI", "b");
    const c = addLayer("s5p", "NO2", "c");

    moveLayer(a, 1);
    expect(useLayersStore.getState().layers.map((l) => l.id)).toEqual([b, a, c]);
    moveLayer(c, -1);
    expect(useLayersStore.getState().layers.map((l) => l.id)).toEqual([b, c, a]);
    moveLayer(b, -1); // already at bottom: no-op
    expect(useLayersStore.getState().layers.map((l) => l.id)).toEqual([b, c, a]);
  });

  it("updates opacity and visibility without touching the mint", () => {
    const { addLayer, setOpacity, toggleVisible, setMint } = useLayersStore.getState();
    const a = addLayer("s2", "NDVI", "a");
    setMint(a, mint("https://t/{z}/{x}/{y}"));
    setOpacity(a, 0.3);
    toggleVisible(a);
    const layer = useLayersStore.getState().layers[0]!;
    expect(layer.opacity).toBe(0.3);
    expect(layer.visible).toBe(false);
    expect(layer.mint?.tileUrl).toBe("https://t/{z}/{x}/{y}");
    expect(layer.status).toBe("ready");
  });

  it("tracks mint lifecycle: minting → ready, errors recorded and cleared", () => {
    const { addLayer, setMinting, setMint, setError } = useLayersStore.getState();
    const a = addLayer("s2", "NDVI", "a");

    setMinting(a);
    expect(useLayersStore.getState().layers[0]!.status).toBe("minting");

    setError(a, "quota exceeded");
    expect(useLayersStore.getState().layers[0]!).toMatchObject({
      status: "error",
      error: "quota exceeded",
    });

    setMinting(a);
    setMint(a, mint("https://t2/{z}/{x}/{y}"));
    expect(useLayersStore.getState().layers[0]!).toMatchObject({
      status: "ready",
      error: null,
    });
  });
});
