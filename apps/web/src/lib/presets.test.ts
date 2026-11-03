import { describe, expect, it } from "vitest";
import { defaultForm } from "../stores/timelapseStore";
import { activePreset, PRESETS, presetById, presetModifiesPixels } from "./presets";

describe("presets", () => {
  it("a fresh form matches no preset (Custom)", () => {
    expect(activePreset(defaultForm())).toBeNull();
  });

  it("recognises a preset once its full recipe is applied, and drops it on divergence", () => {
    const survey = presetById("survey")!;
    const applied = { ...defaultForm(), ...survey.patch };
    expect(activePreset(applied)?.id).toBe("survey");
    // Change one knob the preset set → back to Custom.
    expect(activePreset({ ...applied, composite: "mean" })).toBeNull();
  });

  it("flags the display-only presets (honesty wall gate)", () => {
    expect(presetModifiesPixels(presetById("showcase")!)).toBe(true);
    expect(presetModifiesPixels(presetById("survey")!)).toBe(true);
    expect(presetModifiesPixels(presetById("seasonal")!)).toBe(true);
    // "Every pass" never grades or fills — safe on scientific products.
    expect(presetModifiesPixels(presetById("everypass")!)).toBe(false);
  });

  it("every preset carries teaching copy", () => {
    for (const p of PRESETS) {
      expect(p.tagline.length).toBeGreaterThan(0);
      expect(p.policy.length).toBeGreaterThan(0);
      expect(p.chips.length).toBeGreaterThan(0);
    }
  });
});
