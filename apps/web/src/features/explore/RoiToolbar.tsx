import { usePresets } from "../../api/queries";
import { useMapContext } from "../../map/MapContext";
import type { DrawApi } from "../../map/useTerraDraw";
import { useDateStore } from "../../stores/dateStore";
import { boundsToBBox, roiBounds, useRoiStore } from "../../stores/roiStore";

function roiSummary(roi: ReturnType<typeof useRoiStore.getState>["roi"]): string {
  if (!roi) return "Whole globe";
  if (roi.kind === "bbox") {
    return `BBox ${roi.west.toFixed(2)}, ${roi.south.toFixed(2)} → ${roi.east.toFixed(2)}, ${roi.north.toFixed(2)}`;
  }
  return `Polygon · ${roi.coordinates.length} vertices`;
}

export function RoiToolbar({ draw }: { draw: DrawApi }) {
  const { map } = useMapContext();
  const { data: presets } = usePresets();
  const roi = useRoiStore((state) => state.roi);
  const presetName = useRoiStore((state) => state.presetName);

  const fitTo = (target: NonNullable<typeof roi>) => {
    const [west, south, east, north] = roiBounds(target);
    map?.fitBounds(
      [
        [west, south],
        [east, north],
      ],
      { padding: 40, duration: 600 },
    );
  };

  const applyPreset = (name: string) => {
    const preset = presets?.find((p) => p.name === name);
    if (!preset) return;
    const bbox = boundsToBBox(
      preset.bbox.west,
      preset.bbox.south,
      preset.bbox.east,
      preset.bbox.north,
    );
    draw.clear();
    useRoiStore.getState().applyPreset(preset.name, bbox);
    if (preset.date_hint) {
      const [start, end] = preset.date_hint;
      useDateStore.getState().setMode("range");
      useDateStore.getState().setRange(start, end);
    }
    fitTo(bbox);
  };

  const categories: { key: string; label: string }[] = [
    { key: "continent", label: "Continents" },
    { key: "city", label: "Cities" },
    { key: "methane_site", label: "Methane sites" },
  ];

  return (
    <div className="roi-toolbar">
      <div className="roi-buttons">
        <button
          className={draw.mode === "rectangle" ? "active" : ""}
          title="Draw a bounding box"
          onClick={() => draw.setMode(draw.mode === "rectangle" ? "static" : "rectangle")}
        >
          ▭ BBox
        </button>
        <button
          className={draw.mode === "polygon" ? "active" : ""}
          title="Draw a polygon (click vertices, click the first point to close)"
          onClick={() => draw.setMode(draw.mode === "polygon" ? "static" : "polygon")}
        >
          ⬠ Polygon
        </button>
        <button
          title="Clear the ROI (back to whole globe)"
          disabled={!roi}
          onClick={() => {
            draw.clear();
            useRoiStore.getState().clear();
          }}
        >
          Clear
        </button>
        <button title="Fit the map to the ROI" disabled={!roi} onClick={() => roi && fitTo(roi)}>
          Fit
        </button>
      </div>
      <select
        value={presetName ?? ""}
        onChange={(event) => applyPreset(event.target.value)}
        title="ROI presets (methane sites also set their known-event dates)"
      >
        <option value="" disabled>
          Presets…
        </option>
        {categories.map(({ key, label }) => (
          <optgroup key={key} label={label}>
            {presets
              ?.filter((p) => p.category === key)
              .map((p) => (
                <option key={p.name} value={p.name}>
                  {p.name}
                </option>
              ))}
          </optgroup>
        ))}
      </select>
      <p className="muted roi-summary">{roiSummary(roi)}</p>
    </div>
  );
}
