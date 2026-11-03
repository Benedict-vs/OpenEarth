/**
 * Timelapse presets — the "Look" step. A preset is a *full recipe*: it moves the
 * knobs the Advanced panel exposes, it never hides them (once applied, every value
 * stays editable). The API treats `preset` as a provenance label only, so expansion
 * to individual knobs happens here, in the client.
 *
 * `patch` is both the values a card applies AND the values it is recognised by:
 * `activePreset(form)` returns the preset whose every `patch` key still matches the
 * form, or `null` ("Custom") once the user diverges. Presets are therefore purely
 * derived state — no "dirty" flag to keep in sync.
 */
import type { TimelapseForm } from "../stores/timelapseStore";

export interface Preset {
  id: string;
  name: string;
  /** Card glyph — a plain unicode mark, tinted by CSS. */
  glyph: string;
  /** One line: what you get. */
  tagline: string;
  /** Two–three sentences: exactly what the recipe does, in plain language. */
  policy: string;
  /** Short capability chips shown on the card. */
  chips: string[];
  /** The knob bundle this preset applies and is recognised by. */
  patch: Partial<TimelapseForm>;
}

export const PRESETS: Preset[] = [
  {
    id: "showcase",
    name: "Showcase",
    glyph: "◆",
    tagline: "Broadcast-quality hero clip.",
    policy:
      "Clearest-pixel composite, thin cloud gaps filled from clear days within two windows, exposure flicker evened out, and a cinematic grade. Every borrowed pixel is logged in the frame data.",
    chips: ["Clearest", "Gap-fill 2w", "Deflicker", "Cinematic"],
    patch: {
      composite: "clearest",
      cloudMode: "fill",
      deflicker: true,
      fallback: true,
      gradeCurve: "cinematic",
      gradeBrightness: 0,
      gradeContrast: 0.12,
      gradeSaturation: 1.05,
    },
  },
  {
    id: "survey",
    name: "Survey",
    glyph: "▦",
    tagline: "Honest record, gaps shown.",
    policy:
      "Median composite, no fill. Cloudy windows stay visible as flagged gaps so you can see exactly where the data is thin. Nothing is invented or smoothed.",
    chips: ["Median", "Gaps tinted", "No grade"],
    patch: {
      composite: "median",
      cloudMode: "tint",
      tintColor: "#e34a6f",
      deflicker: false,
      fallback: false,
      gradeCurve: "natural",
      gradeBrightness: 0,
      gradeContrast: 0,
      gradeSaturation: 1,
    },
  },
  {
    id: "everypass",
    name: "Every pass",
    glyph: "▚",
    tagline: "The raw revisit rhythm.",
    policy:
      "The rawest timelapse — each window shown as-is with no compositing, fill, or smoothing. Flickery by design, and true to the satellite's real revisit cadence.",
    chips: ["Per window", "No smoothing"],
    patch: {
      stepMode: "interval",
      composite: "mean",
      cloudMode: "show",
      deflicker: false,
      fallback: false,
      gradeCurve: "natural",
      gradeBrightness: 0,
      gradeContrast: 0,
      gradeSaturation: 1,
    },
  },
  {
    id: "seasonal",
    name: "Seasonal pulse",
    glyph: "❋",
    tagline: "The quarterly rhythm of a place.",
    policy:
      "One clear frame per season — crops greening, snow advancing, reservoirs filling. Clearest composite, light deflicker, and a vivid grade.",
    chips: ["Clearest", "Quarterly", "Vivid"],
    patch: {
      stepMode: "quarterly",
      composite: "clearest",
      cloudMode: "fill",
      deflicker: true,
      fallback: true,
      gradeCurve: "vivid",
      gradeBrightness: 0,
      gradeContrast: 0.06,
      gradeSaturation: 1.2,
    },
  },
];

export function presetById(id: string | null): Preset | undefined {
  return PRESETS.find((p) => p.id === id);
}

/**
 * True when a preset applies a display-only modifier (gap-fill, tint, deflicker,
 * or a non-natural grade). Those are refused on scientific products (the honesty
 * wall), so such presets are disabled when the product isn't RGB.
 */
export function presetModifiesPixels(preset: Preset): boolean {
  const p = preset.patch;
  return (
    p.cloudMode === "fill" ||
    p.cloudMode === "tint" ||
    p.deflicker === true ||
    (p.gradeCurve !== undefined && p.gradeCurve !== "natural") ||
    (p.gradeBrightness ?? 0) !== 0 ||
    (p.gradeContrast ?? 0) !== 0 ||
    (p.gradeSaturation ?? 1) !== 1
  );
}

/** The preset whose every `patch` key still matches the form, or `null` (Custom). */
export function activePreset(form: TimelapseForm): Preset | null {
  return (
    PRESETS.find((preset) =>
      (Object.keys(preset.patch) as (keyof TimelapseForm)[]).every(
        (key) => form[key] === preset.patch[key],
      ),
    ) ?? null
  );
}
