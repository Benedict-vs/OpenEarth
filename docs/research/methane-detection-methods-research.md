# Methane detection methods — literature & industry research dossier

*Research session 2026-07-11 (Fable). Input for a future planning session — not an execution
plan. Everything externally checkable below was web-verified this session; the source list is
at the bottom. Repo-state claims refer to branch `v2/phase7-science-fixes`.*

The four questions this answers:

1. **How do companies like Kayrros find and analyse methane leaks?** (§1)
2. **MBMP needs a plume-free reference — how do literature and industry choose one?** (§2)
3. **Why does MBSP show nothing where RGB clearly shows a flame/plume?** (§3)
4. **If there is no methane, can we check other gases with the data we have?** (§4)

§5 collects validation datasets and detection-limit numbers; §6 distills candidate work items
mapped onto the repo for the planning session.

---

## 1. How industry does it

### 1.1 Kayrros — the published pipeline (Ehret et al. 2022, ES&T / arXiv:2110.11832)

Kayrros' Sentinel-2/Landsat-8 pipeline is published in detail (full text extracted from the
arXiv PDF this session). It is *not* MBMP-with-one-reference — it is a **multi-temporal
regression background**, and this is the single most important finding of this research:

**Preprocessing** (per ~10×10 km AOI):
- L1C time series, preferably **≥ 6 months** deep.
- Co-registration of all images in the series (Hessel et al. method).
- Cloud detection; **discard images with > 15 % cloud pixels**.
- **Anti-aliasing: Gaussian filter σ = 0.7 on B11/B12 before any ratioing** — S2 SWIR bands
  are aliased and the ratio amplifies the aliasing into large artifacts (their Fig. 6).
- Work on the **log band ratio** `log(B12/B11)`; the log compresses abnormally high SWIR
  values "for example due to flaring, which are frequently found in the vicinity of oil and
  gas facilities".

**Background estimation** (the reference-problem solution — see §2.3):
- Sliding window of **T = 30 dates**. For date *t*, the background is the least-squares
  linear projection of the log-ratio image `I_t` onto the previous T−1 images:
  `min_{w_i} ‖I_t − Σ_{i<t} w_i·I_i‖²`; background = `Σ w_i·I_i`; **residual = I_t − background**
  is the detection field. Quote: *"by projecting on a time series there is no need to manually
  choose a reference date as background."*
- **Two-step robust fit**: project once, discard the 5 % worst-fit pixels, refit. (Huber /
  IRLS were tried and judged too slow; the two-step approximation halved background MSE in
  their example.) Methane in *historical* images is handled by the same outlier logic — a
  plume present in one of 29 past images gets little weight.
- **Spatially adaptive variant for changing surfaces** (crop fields): GMM clustering of pixels
  on 4 features (temporal std of the absorbing band, temporal median, x, y), number of
  clusters by BIC, **one projection per cluster**. Only run where albedo variance is high.

**Detection validation — manual.** Quote: *"directly detecting on the residuals … yielded too
many false detections. This is why we added an extra step where all detections are done and
verified manually."* Their checks per candidate:
- The anomaly shape must **not** appear in bands insensitive to CH4 (all bands resampled to
  20 m for this comparison).
- Must be a genuine **dimming in B12** (snow has R(B11) > R(B12) and produces contrast
  inversion in the ratio → dimming-like false positives).

**Quantification:**
- Per-pixel ΔΩ by inverting a Beer–Lambert band-integral (HITRAN cross-sections + ESA-published
  S2A/S2B SRFs, downhill simplex), background column fixed at 1800 ppb; a "pure methane
  atmosphere" approximation is used and judged negligible vs other errors.
- IME with `L = sqrt(A·#M)`, ERA5 10 m wind at the closest time *before* sensing. Source
  origin picked manually from wind + plume shape.
- **Uncertainty by plume transplantation**: simulate the *same* plume into other dates of the
  time series and take the spread of retrieved Q. (Elegant; directly portable to our
  chip-stack world.)

**Scale & framing:** ~1200 events detected over mainly 3 countries (public dataset); merged
with TROPOMI ultra-emitters (Lauvaux et al. 2022, Science: > 25 t/h) and airborne surveys
(Duren, Cusworth) to validate a **power-law emitter size distribution from 0.1 to 600 t/h** —
the argument that monitoring the big emitters constrains the total. Commercially this powers
Kayrros Methane Watch; the tiering is TROPOMI (find hotspots, daily, 5.5×7 km) → S2/L8/S3
(localize + quantify, 20–30 m) → PRISMA/EnMAP/EMIT where available.

### 1.2 SRON / TROPOMI automation (Schuit et al. 2023, ACP)

Two-step ML on TROPOMI XCH4: a **CNN detects plume-like structures**, then an **SVC separates
real plumes from retrieval artifacts** (albedo/aerosol artifacts are endemic in TROPOMI CH4).
Trained on manually labeled 2018–2020 data (public on Zenodo, see §5). Found 2974 plumes in
2021; feeds GHGSat targeted follow-up. Mirrors our architecture lesson: ML ranks candidates,
humans/physics confirm.

### 1.3 UNEP IMEO MARS (operational, public)

Uses 30+ satellite instruments. Pipeline: TROPOMI hotspot detection → high-res attribution
(PRISMA, S2, EMIT, EnMAP) → **custom ML plume-identification models** → human analysts →
notification to governments/operators → public "Eye on Methane" release 30 days later. Again:
ML for triage, humans in the loop for anything outward-facing.

### 1.4 Orbio Earth (S2-first commercial, **open-sourced pipeline**)

Orbio detects from S2 B11/B12 with "temporal and spectral deviation algorithms" + land-cover
classification from visual bands; GEOS-FP winds for quantification; pressure/water-vapor/cloud
data for filtering. In the Stanford single-blind test they caught every release above their
threshold (missed only one < 0.01 t/h, far below any S2 limit).

**Project Eucalyptus** (github.com/Orbio-Earth/Project-Eucalyptus): trained **segmentation
models for S2, Landsat 8/9 and EMIT**, a **synthetic plume generator** (injects physically
realistic CH4 signatures into clean scenes for training/stress-testing), inference notebooks,
and benchmarking tools. **License: non-commercial** (like CH4Net's wall — usable for study,
check before deriving anything we'd publish; contact info@orbio.earth for commercial terms).
Worth reading their synthetic-injection code even if we train nothing from it.

### 1.5 Others, briefly

- **GHGSat**: proprietary Fabry–Pérot imaging spectrometer (~25–50 m, ~kg/h-class limits),
  matched-filter retrieval; takes targeting cues from TROPOMI/SRON. The comparison point for
  "purpose-built beats borrowed bands".
- **Carbon Mapper / JPL**: AVIRIS-NG + Tanager-1 hyperspectral; operational ML plume
  delineation (arXiv 2511.07719, 2505.21806) on matched-filter retrievals; public plume
  portal + Zenodo dataset.
- **MethaneSAT (EDF)**: lost contact 2025-06-20, mission declared over — the gap between
  TROPOMI and point-source imagers it was meant to fill is open again, which strengthens the
  case for squeezing S2/S3.

---

## 2. The MBMP reference problem — solution taxonomy

Our current `pick_reference` (scenes.py) is **metadata-only**: cloud %, |Δt| window, same-orbit
(+30) and same-spacecraft (+5) penalties, nearest wins; `min_days` excludes the same-overpass
tile. It never looks at the pixels, so it cannot know whether the reference (a) contains a
plume, (b) had different surface moisture/snow/vegetation, (c) actually minimizes MBMP noise.
The literature offers four escalating answers:

### 2.1 Manual / event-anchored selection (Varon et al. 2021)

The original MBMP paper picked references **by hand**: scenes "before the emissions began"
(and, as a robustness check, after they ceased), verified visually. No algorithmic criterion
is given at all. Per-satellite references (S2A ref for S2A target, S2B for S2B) to keep SRFs
matched — which our +5 spacecraft penalty mirrors. Take-away: the published MBMP baseline
never solved reference selection; everyone since has bolted something on.

### 2.2 Data-driven pairwise scoring (Gorroño et al. 2023)

Two schemes, tested against simulated truth:
- **Conservative**: the single closest cloud-free overpass with the **same viewing geometry**.
- **Supervised**: the **average of the two nearest acquisitions** (~5 d apart), manually
  screened — averaging two references lowered scene noise from 151.5 to 139.1 ppb at Hassi
  Messaoud.
- Recommendation is explicitly **case-by-case**: closest-in-time if the surface changes fast;
  same-viewing-geometry if the surface is strongly directional. S2A/S2B SRF mismatch measurable
  but small over homogeneous desert.

The obvious automation (my synthesis, consistent with their framework): fetch k candidate
references, compute ΔR_MBMP against each, and **select by minimum robust scene noise σ**
(possibly excluding a plume-shaped region near the site), rather than by metadata score. A
high best-σ is itself a quality flag ("no good reference exists this window").

### 2.3 Multi-temporal regression background (Ehret/Kayrros — the industrial answer)

Full detail in §1.1. Properties that matter for us:
- **No single reference exists** — the background is a fitted linear combination of ~29 prior
  scenes, so no one scene must be plume-free; outlier rejection (drop worst 5 % pixels, refit)
  plus the sheer depth of the stack makes stray plumes and transient surface change nearly
  harmless.
- Solves the **continuous-source problem** (Hassi-Messaoud-style blowouts have no in-period
  plume-free reference; a 30-date window reaches back before the event).
- Cost: T chip fetches per analysis instead of 2, co-registration, and it produces a
  *residual* field rather than a ΔR difference — our LUT inversion consumes it the same way
  (Ehret invert the residual of the log-ratio directly; our ΔR→ΔΩ path is an equivalent
  formulation).
- Failure mode: large coherent albedo change (crops) → their per-cluster GMM projection.

### 2.4 Learned backgrounds (the ML end)

- **Rouet-Leduc & Hulbert 2024 (Nat. Comms)**: ViT encoder + U-Net decoder detects plumes from
  a **single S2 image** (no reference at all — the network learns what surface vs plume looks
  like); claims sensitivity down to 0.2–0.3 t/h, an order of magnitude below band-ratio methods.
- **CH4Net (Vaughan et al. 2024)** — already our ML tier's training source; multi-date input.
- **S2MetNet** (RSE 2023): deep-learning *quantification* benchmark on S2 with a large
  simulated-plume dataset.
- **AttMetNet** (arXiv 2512.02751): attention U-Net on S2, 2025-vintage baseline comparison.
- Orbio's Eucalyptus models (§1.4): segmentation over temporal stacks trained on synthetic
  plume injection — the productionized version of this idea.

### What this means for OpenEarth (candidate ladder)

1. **Cheap (days)**: make `pick_reference` data-driven — score the top-k metadata candidates by
   robust σ of the resulting ΔR_MBMP chip (we already fetch chips through `computePixels`;
   k extra chip fetches, cacheable). Surface the winning reference + its σ in the detection
   detail as provenance. Add "no acceptable reference (σ > threshold)" as an explicit outcome.
2. **Medium (a week)**: multi-reference ensemble — retrieve against the best 2–3 references,
   take the pixelwise **median ΔR_MBMP** (robust to a plume hiding in any one reference;
   Gorroño's 2-reference averaging is the k=2 version of this).
3. **Substantial (a phase)**: Ehret-style regression background over a T≈10–30 date chip stack
   (`GridSpec` already gives us aligned stacks; add the σ=0.7 anti-alias blur + log-ratio +
   two-step outlier refit; per-cluster projection can wait). This subsumes MBSP/MBMP as the
   third retrieval mode and is the literature-backed fix for both reference sensitivity *and*
   MBSP structure noise.

---

## 3. "I can see the flame and plume in RGB but MBSP shows nothing"

Several distinct physical effects stack up here; a plan should treat them separately.

### 3.1 What is actually visible in RGB

**Methane is optically invisible at RGB wavelengths** — B2/B3/B4 sit nowhere near a CH4
absorption band (Ehret Fig. 1: only B11/B12 respond). What one sees in RGB at a flare site is
the **flame's own glow, black-carbon smoke, and possibly a condensation plume** (water vapor).
None of those are methane, and their presence says nothing about whether CH4 is escaping.

### 3.2 A lit flare often means there genuinely is no methane plume

A flare's whole job is to combust CH4 to CO2. Field measurements (Plant et al. 2022, Science —
airborne survey of Permian/Bakken/Eagle Ford): flares average **~91 % combustion efficiency**
(not the assumed 98 %), malfunctioning ones go as low as ~60 %, and **3–5 % of flares are
unlit** (pure venting). Irakulis-Loitxate et al. 2022 (Turkmenistan): **24 of 29 detected
super-emitters were inactive/unlit flares venting gas**. Varon et al. 2021 report the Hassi
Messaoud plume becoming *undetectable* precisely when a flare was lit — the flame appearing in
imagery marked the *end* of the methane release. So: **flame visible + no MBSP signal can be
the physically correct answer.** The 9 % uncombusted fraction of a small flare is usually far
below S2's ~1–3 t/h floor. The interesting monitoring signal at a flare site is the *state
transition* (lit → unlit = venting starts).

### 3.3 But the hot flare also corrupts the retrieval locally

A flame at ~1800 K emits thermally right in the SWIR: the hot pixels gain radiance in B11/B12
(B12 more, relative to solar reflectance) on top of reflected sunlight. Consequences for our
MBSP as implemented:
- The hot pixel's ΔR = (c·R12 − R11)/R11 spikes strongly **positive** — opposite sign to a
  plume, so it is never masked as one, but
- it **biases the zero-intercept fit `c`** (one refit excluding |ΔR| > 1σ may not remove a
  many-σ hot cluster's leverage on the first fit), and
- it **inflates the robust σ** the plume threshold is built on, raising the detection floor
  exactly where the plume would be.
- In MBMP, a flare that changed state between target and reference leaves a large ± dipole.

Literature handling:
- Ehret/Kayrros: the **log-ratio** explicitly to compress flare highs (§1.1).
- The **NHI family** (Marchese, Genzano et al.): Normalized Hotspot Index on exactly our bands,
  `NHI_SWIR = (B12 − B11)/(B12 + B11) > 0` flags thermally emitting pixels; the GEE-based
  **DAFI** system runs this globally with a temporal-persistence criterion for flare sites
  (99 % detection of stable flares, 20–30 m). This is directly implementable on our chips —
  the bands are already in the npz.
- VIIRS Nightfire (Elvidge) is the standard global flare *inventory* if we ever want site
  metadata rather than per-scene masking.

**Candidate fix (small, high value):** compute an NHI hot-pixel mask on every chip; exclude
those pixels (+ a 1–2 px dilation) from the `c` fit, σ estimation, and plume mask; report
"flare lit/unlit" per scene as a detection-evidence field. This kills the σ inflation, gives
the user the §3.2 story in the UI, and creates the lit→unlit transition signal.

### 3.4 And MBSP is structurally weak anyway

Numbers to keep expectations honest (both papers):
- Varon 2021: MBSP precision ~27 % of background over homogeneous desert but **> 200 % over
  urban/farmland**; MBMP ~21–27 %; empirical minimum detectable source 2.6 t/h (Hassi
  Messaoud), 3.5 t/h (Korpezhe). MBSP occasionally *beats* MBMP when inter-pass B12
  variability is large — consistent with what we see per-site.
- Gorroño 2023: retrieval noise 151–252 ppb over homogeneous scenes vs **1488 ppb over the
  Permian** (~10×); detection threshold 1–2 t/h homogeneous, **5–10 t/h heterogeneous**;
  surface structure under the plume becomes the *dominant* error term for heterogeneous or
  temporally varying scenes.
- Plus the aliasing point (§1.1): un-blurred B11/B12 ratios contain aliasing artifacts —
  we currently ratio unfiltered chips; part of our MBSP "structure noise" may literally be
  aliasing, fixable with one Gaussian blur.

So over a typical industrial site (mixed surfaces, roads, tanks), a real few-t/h plume can sit
comfortably inside MBSP's noise while being obvious to the eye in fortunate RGB contrast.
This is the expected behavior of the published method, not a bug in our port — and it is why
every operational player moved to multi-temporal or learned backgrounds (§2.3–2.4).

---

## 4. No methane? What other gases the data can(not) give us

### 4.1 Sentinel-2 itself: CH4 only — hard physics limit

S2 MSI has exactly two CH4-sensitive bands and **no usable band for any other trace gas**:
CO2's 2.0 µm band falls *between* B11 (1.56–1.66 µm) and B12 (2.11–2.29 µm); its 1.6 µm band
is far too weak; NO2/SO2/CO absorb in spectral regions MSI doesn't resolve. Varon 2021 note
B11/B12 can't even separate albedo–H2O–CO2–CH4 simultaneously — the two-band trick works only
because we *assume* the anomaly is CH4. There is no "other gas" mode for S2; anything more
needs hyperspectral (60+ SWIR channels) or a dedicated sounder.
Corollary worth stating in docs: a ΔR anomaly is *attributed* to CH4, not proven to be CH4 —
one more reason the co-emission evidence below is valuable.

### 4.2 S5P/TROPOMI: five more gases already in our catalog

`catalog/builtin/s5p.py` already ships **NO2, SO2, CO, O3, HCHO** alongside CH4 — the data
plumbing for a multi-gas story exists today. The literature use-cases:
- **NO2 = combustion tracer.** Flaring and engines emit NOx; TROPOMI NO2 enhancement
  co-located with a facility indicates *active combustion* (complements §3: lit flare → NO2 up,
  CH4 down; venting → CH4 up, NO2 absent). Used as flaring/combustion-efficiency proxy in the
  TROPOMI literature (e.g. the CO+NO2 combustion-efficiency work, ACP 2021).
- **CO = incomplete combustion** (fires, inefficient flares, smoldering).
- **SO2** = sour-gas processing, refineries, volcanic.
- Caveat: 5.5×7 km pixels — facility-scale only for large sources or averaging windows; this is
  *screening evidence*, like our existing TROPOMI CH4 tier, not plume imaging.

**Candidate feature ("combustion evidence panel"):** for a methane site/detection, sample S5P
NO2 + CO over the site window vs a background annulus (the machinery of `tropomi.py` screening
generalizes almost verbatim) and show it beside the CH4 verdict: "no CH4, but NO2 elevated →
lit flare, combustion confirmed" / "CH4 up, NO2 flat → venting". That turns the §3.2 physics
into a UI story.

### 4.3 EMIT: CO2 plumes, same plumbing as our Phase 6 CH4 tier

LP DAAC ships **EMITL2BCO2ENH** (V002: per-pixel CO2 enhancement in ppm·m, matched-filter, with
UNCERT + SENS layers, all granules) and **EMITL2BCO2PLM** (V001: identified plume complexes,
COGs + GeoJSON metadata) — the exact CO2 analogues of the CH4ENH/CH4PLM products we already
consume. **Not mirrored in GEE** (checked — only CH4 is), so this rides the existing
earthaccess V002 path in `services/emit.py`, not the GEE mirror. CO2 point sources = power
plants, gas processing, flares (a lit flare is a CO2 point source!). A "CO2 plumes near site"
extension of the Phase 6 cross-match is mostly parameter plumbing: new short_name, same
tolerant-GeoJSON parser shape, same ≤5 km/≤3 d cross-match idea.

### 4.4 Hyperspectral landscape (context, no free real-time API)

PRISMA/EnMAP: proposal-gated tasking, matched-filter CH4+CO2 retrievals in the literature;
Carbon Mapper Tanager: public plume portal + Zenodo archive (usable as *validation* data, like
our IMEO/SRON importer); GHGSat: commercial. None of these change our stack near-term beyond
what EMIT already gives us.

---

## 5. Validation resources & benchmark numbers worth having on file

| Resource | What | Where |
|---|---|---|
| Gorroño et al. simulated S2 plume dataset ("S2CH4") | S2 L1C scenes with embedded WRF-LES plumes at known flux — ground truth for retrieval/masking benchmarks | Harvard Dataverse DOI 10.7910/DVN/KRNPEH |
| Ehret et al. detection dataset | ~1200 S2/L8 events, quantified | linked from arXiv:2110.11832 |
| Schuit et al. labeled TROPOMI plumes | CNN/SVC training labels 2018–2020 + all 2021 detections | Zenodo 13903869 / WUR |
| Carbon Mapper plume list | aerial plume truth (used by the ViT paper) | Zenodo 10.5281/zenodo.7072824 |
| Sherwin et al. 2024 single-blind test (AMT 17, 765) | 9 satellite systems vs metered releases: 55 % of estimates within ±50 % of truth; 95 % CIs contain truth only 52–70 % of the time; smallest space detection 33 kg/h (WorldView-3); teams incl. Kayrros/Orbio/GHGSat | amt.copernicus.org |
| Detection floors (S2) | Varon: 2.6–3.5 t/h empirical; Gorroño: 1–2 t/h homogeneous, 5–10 t/h heterogeneous; ViT paper claims 0.2–0.3 t/h (learned) | see §2–3 |

The Gorroño dataset is the natural next input for our `calibration_harness.py` — known-truth
chips instead of literature-anchor events, and it would let us *measure* (not argue) the gain
from any §2 ladder step. The Sherwin CI-coverage result (52–70 %) is also a useful humility
anchor for how we present our own MC uncertainty bands.

---

## 6. Distilled candidate work items (for the planning session)

Ordered by (value ÷ effort), with repo touch-points:

1. **NHI flare mask + flare-state evidence** (§3.3) — `methane/retrieval.py` (mask into `c`
   fit + σ), `plume.py` (exclude from mask), detection payload + Lab UI chip. Small, kills a
   real failure mode, adds the lit/unlit story. Pure NumPy, offline-testable.
2. **Anti-alias blur σ=0.7 before ratioing** (§1.1/§3.4) — one line in the chip path +
   calibration-harness re-run to re-anchor. Literature-backed noise reduction for *both* MBSP
   and MBMP. (Check effect on the frozen mask LUT contract first — footprint invariance test.)
3. **Data-driven reference selection** (§2 ladder step 1) — extend `pick_reference` with a
   chip-noise scoring stage over top-k candidates; expose chosen-reference σ as provenance;
   explicit "no acceptable reference" outcome. Medium-small; pairs with a `docs` note.
4. **Combustion evidence panel (S5P NO2/CO at site)** (§4.2) — reuse `tropomi.py` sampling
   machinery for NO2/CO; API field on detection detail; Lab UI panel. Medium.
5. **Multi-reference median MBMP** (§2 ladder step 2) — pixelwise median over 2–3 best
   references. Small increment on top of item 3.
6. **EMIT CO2 cross-match** (§4.3) — clone of the Phase 6 V002 earthaccess path with CO2
   short_names; UI chip next to the CH4 match. Medium; zero new science.
7. **Ehret-style multi-temporal regression background** (§2.3) — a phase of its own; the
   strategic fix for reference selection + heterogeneous-surface noise; enables continuous
   sources. Needs: chip-stack fetcher (T dates on one `GridSpec`), log-ratio + projection +
   two-step refit (pure NumPy, very testable), LUT-inversion adapter, cache strategy.
8. **Benchmark harness on the Gorroño S2CH4 dataset** (§5) — measure items 2/3/5/7 instead of
   asserting them. Fits `scripts/calibration_harness.py` pattern.
9. *(Later/optional)* Single-image learned detector à la ViT paper for reference-free
   screening — only after 7, and mind the license walls (§1.4, CH4Net memory).

Cross-cutting honesty note for docs: even Kayrros validates every detection manually (§1.1),
and MARS/SRON keep humans in the loop. Our "ML = candidate ranker requiring human review"
stance is the industry norm, not a limitation to apologize for.

---

## Sources

**Core methods:**
- Varon et al. 2021, *High-frequency monitoring of anomalous methane point sources with
  multispectral Sentinel-2 satellite observations*, AMT 14, 2771 — https://amt.copernicus.org/articles/14/2771/2021/
- Ehret et al. 2022, *Global Tracking and Quantification of Oil and Gas Methane Emissions from
  Recurrent Sentinel-2 Imagery*, ES&T — https://arxiv.org/abs/2110.11832 (full text extracted)
- Gorroño et al. 2023, *Understanding the potential of Sentinel-2 for monitoring methane point
  emissions*, AMT 16, 89 — https://amt.copernicus.org/articles/16/89/2023/
- Pandey et al. 2023, *Daily detection and quantification of methane leaks using Sentinel-3…
  tiered approach with Sentinel-2 and Sentinel-5p*, RSE — https://arxiv.org/abs/2212.11318
- Lauvaux et al. 2022, *Global assessment of oil and gas methane ultra-emitters*, Science.

**Industry / operational:**
- Orbio Earth: https://business.esa.int/projects/orbio-earth-platform ,
  https://github.com/Orbio-Earth/Project-Eucalyptus
- UNEP IMEO MARS: https://www.unep.org/topics/energy/methane/methane-alert-and-response-system-mars
- Schuit et al. 2023, *Automated detection and monitoring of methane super-emitters using
  satellite data*, ACP 23, 9071 — https://acp.copernicus.org/articles/23/9071/2023/
- Sherwin et al. 2024, *Single-blind test of nine methane-sensing satellite systems*, AMT 17,
  765 — https://amt.copernicus.org/articles/17/765/2024/

**Flares & other gases:**
- Plant et al. 2022, *Inefficient and unlit natural gas flares both emit large quantities of
  methane*, Science — https://www.science.org/doi/10.1126/science.abq0385
- Irakulis-Loitxate et al. 2022, *Satellites Detect Abatable Super-Emissions…*, ES&T —
  https://pmc.ncbi.nlm.nih.gov/articles/PMC9940854/
- NHI / DAFI gas-flaring detection (Marchese, Genzano et al.) —
  https://www.mdpi.com/2072-4292/14/24/6319 , https://sites.google.com/view/flaringsitesinventory ,
  https://doi.org/10.3390/s23125734
- EMIT CO2 products — https://lpdaac.usgs.gov/products/emitl2bco2plmv001/ ,
  https://www.earthdata.nasa.gov/data/catalog/lpcloud-emitl2bco2enh-002
- TROPOMI CO/NO2 combustion efficiency — https://acp.copernicus.org/articles/21/597/2021/

**ML detectors:**
- Rouet-Leduc & Hulbert 2024, *Automatic detection of methane emissions in multispectral
  satellite imagery using a vision transformer*, Nat. Comms —
  https://www.nature.com/articles/s41467-024-47754-y
- Vaughan et al. 2024, *CH4Net*, AMT 17, 2583 — https://amt.copernicus.org/articles/17/2583/2024/
- S2MetNet, RSE 2023 — https://www.sciencedirect.com/science/article/abs/pii/S0034425723002596
- AttMetNet — https://arxiv.org/abs/2512.02751
- JPL operational GHG plume ML — https://arxiv.org/abs/2511.07719 , https://arxiv.org/abs/2505.21806
