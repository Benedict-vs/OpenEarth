import { useAois, useDeleteAoi, usePresets, useSaveAoi } from "../../api/queries";
import type { Aoi } from "../../api/types";
import { ApiError } from "../../api/client";
import { useMapContext } from "../../map/MapContext";
import type { DrawApi } from "../../map/useTerraDraw";
import { rangeToWindow } from "../../lib/timeWindow";
import { useDateStore } from "../../stores/dateStore";
import { boundsToBBox, roiBounds, useRoiStore } from "../../stores/roiStore";

const SAVED_PREFIX = "saved:";

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
  const { data: aois } = useAois();
  const saveAoi = useSaveAoi();
  const deleteAoi = useDeleteAoi();
  const roi = useRoiStore((state) => state.roi);
  const presetName = useRoiStore((state) => state.presetName);

  const savedByName = new Map((aois ?? []).map((a) => [a.name, a]));
  const currentSaved = presetName != null ? savedByName.get(presetName) : undefined;
  // The <select> value: saved AOIs are namespaced so they can share a name with
  // a built-in preset without the dropdown confusing the two.
  const selectValue =
    presetName == null ? "" : currentSaved ? `${SAVED_PREFIX}${presetName}` : presetName;

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
      // A site hint is a season, not a composite: it sets the period, and a
      // window centered in it but capped at ±45 d so the composite stays legible.
      const w = rangeToWindow(start, end);
      useDateStore.getState().setPeriod(start, end);
      useDateStore.getState().setWindow({ center: w.center, halfDays: Math.min(w.halfDays, 45) });
    }
    fitTo(bbox);
  };

  const applySavedAoi = (aoi: Aoi) => {
    draw.clear();
    useRoiStore.getState().applyPreset(aoi.name, aoi.roi);
    fitTo(aoi.roi);
  };

  const onPresetChange = (value: string) => {
    if (value.startsWith(SAVED_PREFIX)) {
      const aoi = savedByName.get(value.slice(SAVED_PREFIX.length));
      if (aoi) applySavedAoi(aoi);
    } else {
      applyPreset(value);
    }
  };

  const handleSaveAoi = () => {
    if (!roi) return;
    const name = window.prompt("Name this AOI")?.trim();
    if (!name) return;
    saveAoi.mutate(
      { name, roi },
      {
        onSuccess: () => useRoiStore.getState().applyPreset(name, roi),
        onError: (err) =>
          window.alert(err instanceof ApiError ? err.detail : "Could not save the AOI."),
      },
    );
  };

  const handleDeleteSaved = () => {
    if (!currentSaved) return;
    deleteAoi.mutate(currentSaved.id, {
      // Keep the ROI geometry but drop the now-dangling saved-AOI attribution,
      // so the dropdown falls back to its placeholder, not a stale/first label.
      onSuccess: () => {
        if (roi) useRoiStore.getState().setRoi(roi);
      },
      onError: (err) =>
        window.alert(err instanceof ApiError ? err.detail : "Could not delete the AOI."),
    });
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
        <button
          title="Save the current ROI as a named AOI"
          disabled={!roi || saveAoi.isPending}
          onClick={handleSaveAoi}
        >
          ☆ Save AOI…
        </button>
      </div>
      <div className="roi-preset-row">
        <select
          value={selectValue}
          onChange={(event) => onPresetChange(event.target.value)}
          title="ROI presets and saved AOIs (methane sites also set their known-event dates)"
        >
          <option value="" disabled>
            Presets & saved…
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
          {aois && aois.length > 0 ? (
            <optgroup label="Saved">
              {aois.map((a) => (
                <option key={a.id} value={`${SAVED_PREFIX}${a.name}`}>
                  {a.name}
                </option>
              ))}
            </optgroup>
          ) : null}
        </select>
        {currentSaved ? (
          <button
            className="danger"
            title={`Delete saved AOI “${currentSaved.name}”`}
            disabled={deleteAoi.isPending}
            onClick={handleDeleteSaved}
          >
            Delete
          </button>
        ) : null}
      </div>
      <p className="muted roi-summary">{roiSummary(roi)}</p>
    </div>
  );
}
