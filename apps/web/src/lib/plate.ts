/**
 * The citable "plate" (Phase 10 Stage 5, the Atlas add-on): a finished render's
 * hero still composited with a provenance data sheet into one downloadable PNG.
 *
 * It packages ONLY what the pipeline already recorded — the manifest v2 honesty
 * surfaces (per-frame source / measured / borrowed, composite, coverage) — into
 * a self-contained card. No new "truth" is minted, and nothing is hosted: the
 * plate is a local download, drawn on a canvas from a same-origin still.
 */
import type { RenderDetail, RoiIn } from "../api/types";
import { coverageSummary, frameQc, parseManifest } from "./manifest";

export interface PlateInput {
  heroUrl: string;
  title: string;
  dataset: string;
  product: string;
  start: string;
  end: string;
  centerLat: number;
  centerLon: number;
  composite: string;
  width: number;
  height: number;
  /** The region's native sensor limit (px) when the manifest recorded it. */
  nativeMaxDim: number | null;
  frameLabel: string;
  frameNumber: number; // 1-based
  frameCount: number;
  measured: number | null;
  borrowed: number | null;
  source: string | null;
  renderedCount: number;
  windowCount: number;
  fallbackCount: number;
  blankCount: number;
}

function roiCenter(roi: RoiIn): { lat: number; lon: number } {
  if (roi.kind === "bbox") {
    return { lat: (roi.south + roi.north) / 2, lon: (roi.west + roi.east) / 2 };
  }
  const ring = roi.coordinates as [number, number][];
  const lons = ring.map(([lon]) => lon);
  const lats = ring.map(([, lat]) => lat);
  return { lat: (Math.min(...lats) + Math.max(...lats)) / 2, lon: (Math.min(...lons) + Math.max(...lons)) / 2 };
}

/** Pure extraction of everything the plate needs from a finished render. */
export function plateInputFromDetail(
  detail: RenderDetail,
  frameIndex: number,
  heroUrl: string,
): PlateInput | null {
  const m = parseManifest(detail);
  if (!m) return null;
  const cov = coverageSummary(m);
  const qc = frameQc(m, frameIndex);
  const { lat, lon } = roiCenter(detail.roi);
  const fallbackCount = Object.entries(cov.sources)
    .filter(([src]) => src !== m.dataset)
    .reduce((n, [, c]) => n + c, 0);
  return {
    heroUrl,
    title: detail.title,
    dataset: m.dataset,
    product: m.product,
    start: detail.params?.["dates"] ? (detail.params["dates"] as { start: string }).start : "",
    end: detail.params?.["dates"] ? (detail.params["dates"] as { end: string }).end : "",
    centerLat: lat,
    centerLon: lon,
    composite: m.composite,
    width: m.width,
    height: m.height,
    nativeMaxDim: m.native_max_dim ?? null,
    frameLabel: qc?.label ?? "",
    frameNumber: frameIndex + 1,
    frameCount: cov.rendered,
    measured: qc?.valid ?? null,
    borrowed: qc?.filled ?? null,
    source: qc?.source ?? null,
    renderedCount: cov.rendered,
    windowCount: cov.windows,
    fallbackCount,
    blankCount: cov.empty,
  };
}

// ── Canvas rendering ──

const PAPER = "#0f1216";
const INK = "#ece7dd";
const MUTED = "#8a8578";
const RULE = "#2a2e37";
const BRASS = "#c7a262";
const SURVEY = "#e8613c";

const SERIF = 'Georgia, "Iowan Old Style", "Times New Roman", serif';
const MONO = 'ui-monospace, "SF Mono", Menlo, monospace';
const SANS = 'system-ui, -apple-system, "Segoe UI", sans-serif';

const W = 1280;
const H = 800;
const PAD = 48;

function fmtPct(x: number | null): string {
  return x == null ? "—" : `${(x * 100).toFixed(1)}%`;
}

function fmtCoord(lat: number, lon: number): string {
  const ns = lat >= 0 ? "N" : "S";
  const ew = lon >= 0 ? "E" : "W";
  return `${Math.abs(lat).toFixed(4)}° ${ns} · ${Math.abs(lon).toFixed(4)}° ${ew}`;
}

function loadImage(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error(`Could not load hero still: ${url}`));
    img.src = url;
  });
}

/** Compose the plate PNG. Rejects if the hero still can't be loaded. */
export async function buildPlate(input: PlateInput): Promise<Blob> {
  const hero = await loadImage(input.heroUrl);
  const canvas = document.createElement("canvas");
  canvas.width = W;
  canvas.height = H;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("Canvas 2D unavailable");

  // Background + subtle graticule wash.
  ctx.fillStyle = PAPER;
  ctx.fillRect(0, 0, W, H);
  ctx.strokeStyle = "rgba(255,255,255,0.02)";
  ctx.lineWidth = 1;
  for (let x = 48; x < W; x += 48) line(ctx, x, 0, x, H);
  for (let y = 48; y < H; y += 48) line(ctx, 0, y, W, y);

  // ── Cartouche ──
  text(ctx, input.title || `${input.dataset} · ${input.product}`, PAD, 78, { font: `600 34px ${SERIF}`, color: INK });
  text(ctx, `${fmtCoord(input.centerLat, input.centerLon)} — ${input.start} to ${input.end}`, PAD, 108, {
    font: `13px ${MONO}`,
    color: MUTED,
  });
  const stamp = ["OPENEARTH ATLAS", `${input.dataset.toUpperCase()} · ${input.product.toUpperCase()}`, `PLATE ${input.frameNumber} OF ${input.frameCount}`];
  stamp.forEach((s, i) =>
    text(ctx, s, W - PAD, 66 + i * 18, { font: `11px ${SANS}`, color: BRASS, align: "right", letter: 1.5 }),
  );
  rule(ctx, PAD, 132, W - PAD);

  // ── Figure (letterboxed hero with a graticule frame) ──
  const figX = PAD;
  const figY = 164;
  const figW = 704;
  const figH = 500;
  ctx.strokeStyle = RULE;
  ctx.lineWidth = 1;
  ctx.strokeRect(figX + 0.5, figY + 0.5, figW, figH);
  const inset = 14;
  drawContain(ctx, hero, figX + inset, figY + inset, figW - inset * 2, figH - inset * 2 - 26);
  ctx.strokeRect(figX + inset + 0.5, figY + inset + 0.5, figW - inset * 2, figH - inset * 2 - 26);
  text(ctx, `Fig. ${input.frameNumber} — ${input.frameLabel}`, figX + inset, figY + figH - 12, {
    font: `12px ${SERIF}`,
    color: INK,
  });
  text(ctx, `frame ${input.frameNumber} / ${input.frameCount}`, figX + figW - inset, figY + figH - 12, {
    font: `11px ${MONO}`,
    color: MUTED,
    align: "right",
  });

  // ── Data sheet ──
  const dsX = figX + figW + 40;
  const dsW = W - dsX - PAD;
  let y = figY + 6;
  text(ctx, "DATA SHEET · THIS PLATE", dsX, y, { font: `11px ${SANS}`, color: MUTED, letter: 1.6 });
  y += 20;
  const blank = input.measured == null ? null : Math.max(0, 1 - input.measured - (input.borrowed ?? 0));
  const rows: Array<[string, string, string | undefined]> = [
    ["Source", (input.source ?? "—").toUpperCase(), undefined],
    ["Composite", input.composite, undefined],
    ["Measured", fmtPct(input.measured), undefined],
    ["Borrowed ≤ 2w", fmtPct(input.borrowed), SURVEY],
    ["Blank", fmtPct(blank), undefined],
    [
      "Resolution",
      input.nativeMaxDim != null && Math.max(input.width, input.height) > input.nativeMaxDim
        ? `${input.width}×${input.height} px · native ${input.nativeMaxDim}`
        : `${input.width}×${input.height} px`,
      undefined,
    ],
  ];
  for (const [k, v, c] of rows) y = dataRow(ctx, dsX, y, dsW, k, v, c);

  y += 26;
  text(ctx, "COVERAGE · WHOLE RENDER", dsX, y, { font: `11px ${SANS}`, color: MUTED, letter: 1.6 });
  y += 20;
  const cov: Array<[string, string, string | undefined]> = [
    ["Frames with data", `${input.renderedCount} / ${input.windowCount}`, undefined],
    ["Stepped to fallback", String(input.fallbackCount), input.fallbackCount > 0 ? BRASS : undefined],
    ["Blank windows", String(input.blankCount), input.blankCount > 0 ? SURVEY : undefined],
  ];
  for (const [k, v, c] of cov) y = dataRow(ctx, dsX, y, dsW, k, v, c);

  y += 30;
  text(ctx, "A borrowed pixel is never passed off as measured.", dsX, y, {
    font: `italic 12px ${SERIF}`,
    color: MUTED,
  });

  // ── Footer ──
  rule(ctx, PAD, H - 64, W - PAD);
  text(ctx, `${datasetAttribution(input.dataset)} · Google Earth Engine`, PAD, H - 40, {
    font: `12px ${SANS}`,
    color: MUTED,
  });
  text(ctx, "Compiled with OpenEarth", W - PAD, H - 40, { font: `12px ${SANS}`, color: MUTED, align: "right" });

  return await new Promise<Blob>((resolve, reject) =>
    canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("toBlob failed"))), "image/png"),
  );
}

function datasetAttribution(dataset: string): string {
  if (dataset === "s2") return "Copernicus Sentinel-2";
  if (dataset === "hls") return "NASA HLS (Landsat/Sentinel-2)";
  if (dataset === "landsat") return "USGS/NASA Landsat";
  return dataset.toUpperCase();
}

// ── canvas helpers ──

function line(ctx: CanvasRenderingContext2D, x1: number, y1: number, x2: number, y2: number): void {
  ctx.beginPath();
  ctx.moveTo(x1, y1);
  ctx.lineTo(x2, y2);
  ctx.stroke();
}

function rule(ctx: CanvasRenderingContext2D, x1: number, y: number, x2: number): void {
  ctx.strokeStyle = RULE;
  ctx.lineWidth = 1;
  line(ctx, x1, y + 0.5, x2, y + 0.5);
}

interface TextOpts {
  font: string;
  color: string;
  align?: CanvasTextAlign;
  letter?: number;
}

function text(ctx: CanvasRenderingContext2D, s: string, x: number, y: number, o: TextOpts): void {
  ctx.font = o.font;
  ctx.fillStyle = o.color;
  ctx.textAlign = o.align ?? "left";
  ctx.textBaseline = "alphabetic";
  if (o.letter && "letterSpacing" in ctx) {
    (ctx as CanvasRenderingContext2D & { letterSpacing: string }).letterSpacing = `${o.letter}px`;
    ctx.fillText(s, x, y);
    (ctx as CanvasRenderingContext2D & { letterSpacing: string }).letterSpacing = "0px";
  } else {
    ctx.fillText(s, x, y);
  }
}

/** One dotted key→value row; returns the next y. */
function dataRow(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  key: string,
  value: string,
  valueColor?: string,
): number {
  text(ctx, key, x, y + 14, { font: `13px ${SANS}`, color: MUTED });
  text(ctx, value, x + w, y + 14, { font: `13px ${MONO}`, color: valueColor ?? INK, align: "right" });
  ctx.strokeStyle = RULE;
  ctx.setLineDash([1, 3]);
  line(ctx, x, y + 24.5, x + w, y + 24.5);
  ctx.setLineDash([]);
  return y + 32;
}

/** Draw an image "contain"-fitted (letterboxed) within a box. */
function drawContain(
  ctx: CanvasRenderingContext2D,
  img: HTMLImageElement,
  bx: number,
  by: number,
  bw: number,
  bh: number,
): void {
  const scale = Math.min(bw / img.naturalWidth, bh / img.naturalHeight);
  const dw = img.naturalWidth * scale;
  const dh = img.naturalHeight * scale;
  ctx.fillStyle = "#000";
  ctx.fillRect(bx, by, bw, bh);
  ctx.drawImage(img, bx + (bw - dw) / 2, by + (bh - dh) / 2, dw, dh);
}

/** Trigger a browser download of a blob. */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
