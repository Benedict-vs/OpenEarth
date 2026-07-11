"""Built-in EMIT L2B methane products (GEE V001 mirror, generic pipeline).

EMIT (Earth surface Mineral dust source InvesTigation, on the ISS) carries an
imaging spectrometer whose SWIR bands resolve the 2.3 µm methane absorption at
60 m — far finer than TROPOMI's ~7 km grid. The L2B CH4ENH product is a
matched-filter column *enhancement* over the local background (ppm·m), so its
noise is roughly symmetric about zero and only the positive tail carries plume
signal.

Earth Engine hosts a **frozen V001 mirror** (``NASA/EMIT/L2B/CH4ENH``, band
``vertical_column_enhancement``), covering Aug 2022 – Nov 2024 only; V001 was
decommissioned upstream and the live V002 collection is fetched granule-by-
granule by the EMIT plume tier (see ``methane/emit.py``, Phase 6). CH4ENH needs
no dedicated provider: one raw band + valid-range masking is exactly what the
generic pipeline does, so ``emit`` is *not* special-cased in the dispatcher —
it flows through ``get_generic_collection`` like any user-registered dataset.
"""

from __future__ import annotations

from openearth.catalog.models import DatasetSpec, ProductSpec

EMIT_CH4ENH_COLLECTION_ID = "NASA/EMIT/L2B/CH4ENH"

# Plasma sequential ramp (dark blue → magenta → orange → yellow): reads as
# "plume intensity" and stays visually distinct from the S5P gas palettes.
# Matched-filter noise (values ≤ 0) collapses into the dark low end; the bright
# tail picks out real enhancements.
_PALETTE_PLUME: list[str] = [
    "#0d0887",
    "#46039f",
    "#7201a8",
    "#9c179e",
    "#bd3786",
    "#d8576b",
    "#ed7953",
    "#fb9f3a",
    "#fdca26",
    "#f0f921",
]


EMIT_PRODUCTS: dict[str, ProductSpec] = {
    "CH4ENH": ProductSpec(
        key="CH4ENH",
        name="Methane Column Enhancement",
        collection_id=EMIT_CH4ENH_COLLECTION_ID,
        source_band="vertical_column_enhancement",
        # Display 0–1500 ppm·m: JPL's standard EMIT plume range. Negative
        # matched-filter noise clamps to the dark low end; strong plumes
        # approach saturation (live Permian scenes: p95 ≈ 500–670, p99 ≈
        # 730–925 ppm·m, super-emitter peaks past 2000).
        vis_min=0.0,
        vis_max=1500.0,
        # Generously negative so the full symmetric noise floor survives
        # (live single-scene p0 ≈ −1577 ppm·m); below −2000 is a fill/error
        # value to mask. Upper bound keeps extreme super-emitters (live peak
        # ≈ 7500 ppm·m) rather than clipping them.
        valid_min=-2000.0,
        valid_max=10000.0,
        display_unit="ppm·m",
        description=(
            "**Reading the CH₄ enhancement scale:** "
            "EMIT column methane *enhancement* over the local background, "
            "retrieved by matched filter from the imaging spectrometer's "
            "SWIR bands at 60 m. "
            "**Near-zero and negative values** (dark) are retrieval noise "
            "— the matched filter is roughly symmetric about zero, so only "
            "the positive tail is signal. "
            "**Moderate values** (a few hundred ppm·m) mark the diffuse "
            "edges of a plume. "
            "**High values** (bright yellow, ≳ 1000 ppm·m) mark plume cores "
            "over active point sources such as oil & gas infrastructure, "
            "landfills, or coal mines. "
            "Unlike TROPOMI's ~7 km column, EMIT resolves individual plumes "
            "and their source facilities. "
            "**Coverage note:** this is Earth Engine's frozen **V001 mirror**, "
            "a GEE copy spanning Aug 2022 – Nov 2024; later granules are "
            "served through the EMIT plume fetcher (live V002 collection). "
            "EMIT observes opportunistically from the ISS, so coverage is "
            "sparse and targeted rather than global."
        ),
        display_scale=1.0,
        palette=list(_PALETTE_PLUME),
    ),
}


EMIT_DATASET = DatasetSpec(
    id="emit",
    title="EMIT L2B Methane (ISS)",
    collection_id=EMIT_CH4ENH_COLLECTION_ID,
    attribution="Google Earth Engine / NASA JPL EMIT (V001 mirror)",
    default_scale_m=60,
    products=EMIT_PRODUCTS,
)
