/**
 * Typed latitude/longitude pair (decimal degrees). Like DateField it commits on
 * Enter/blur, not per keystroke, and keeps invalid text visible until fixed.
 * With `optional`, emptying both fields commits `null` (e.g. "no source point").
 */
import { useState } from "react";
import { isValidLatLon, parseNumberInput } from "../../lib/methane";

interface LatLon {
  lat: number;
  lon: number;
}

interface CoordFieldsProps {
  value: LatLon | null;
  onCommit(value: LatLon | null): void;
  optional?: boolean;
  /** Prefix for the accessible names ("Area centre latitude", …). */
  label: string;
}

const fmt = (v: number) => v.toFixed(5).replace(/0+$/, "").replace(/\.$/, "");

export function CoordFields({ value, onCommit, optional = false, label }: CoordFieldsProps) {
  const [draft, setDraft] = useState<{ lat: string; lon: string } | null>(null);
  const shown = draft ?? {
    lat: value ? fmt(value.lat) : "",
    lon: value ? fmt(value.lon) : "",
  };
  const lat = parseNumberInput(shown.lat);
  const lon = parseNumberInput(shown.lon);
  const empty = shown.lat.trim() === "" && shown.lon.trim() === "";
  const ok = isValidLatLon(lat, lon);
  const invalid = draft !== null && !ok && !(optional && empty);

  const commit = () => {
    if (draft === null) return;
    if (optional && empty) {
      setDraft(null);
      if (value !== null) onCommit(null);
      return;
    }
    if (!ok) return; // keep the text (marked invalid) for correction
    setDraft(null);
    if (!value || value.lat !== lat || value.lon !== lon) onCommit({ lat, lon });
  };

  const field = (key: "lat" | "lon", placeholder: string, name: string) => (
    <input
      type="text"
      inputMode="decimal"
      spellCheck={false}
      className={invalid ? "coord-input invalid" : "coord-input"}
      placeholder={placeholder}
      aria-label={`${label} ${name}`}
      aria-invalid={invalid}
      title={invalid ? "Latitude −90…90, longitude −180…180 (decimal degrees)" : undefined}
      value={shown[key]}
      onChange={(e) => setDraft({ ...shown, [key]: e.target.value })}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") commit();
        if (e.key === "Escape") setDraft(null);
      }}
    />
  );

  return (
    <div className="coord-fields">
      {field("lat", "lat °N", "latitude")}
      {field("lon", "lon °E", "longitude")}
    </div>
  );
}
