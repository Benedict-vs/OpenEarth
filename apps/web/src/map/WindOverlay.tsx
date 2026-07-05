/**
 * ERA5 10 m wind arrows drawn on a 2D canvas layered over the map.
 *
 * The canvas lives in the map's own canvas container (imperative, like
 * useInspector / useTerraDraw) so arrows stay pinned to geography as the user
 * pans and zoom — projecting each grid point with `map.project()` on every
 * `move`. Data follows the viewport: a TanStack Query keyed on the bounds
 * rounded to 2 dp plus the active date, so sub-degree jitter doesn't refetch.
 *
 * This is browsing weather context, not overpass-matched wind (a Phase 3
 * concern), so the sampled instant is drawn on the overlay to keep it honest.
 */
import { useQuery } from "@tanstack/react-query";
import type maplibregl from "maplibre-gl";
import { useEffect, useRef, useState } from "react";
import { fetchWindField } from "../api/queries";
import type { WindField } from "../api/types";
import { useDateStore } from "../stores/dateStore";
import { useWindStore } from "../stores/windStore";
import { useMapContext } from "./MapContext";

interface Bounds {
  west: number;
  south: number;
  east: number;
  north: number;
}

// Arrow length (px) grows with speed between these bounds; alpha likewise.
const MIN_LEN = 8;
const MAX_LEN = 28;
const SPEED_TO_PX = 1.6;

const clamp = (x: number, lo: number, hi: number): number => Math.min(hi, Math.max(lo, x));

/** Viewport bounds rounded to 2 dp and clamped to the valid lon/lat domain.
 *  Returns null for a degenerate/antimeridian-crossing view (skip the fetch). */
function viewportBounds(map: maplibregl.Map): Bounds | null {
  const b = map.getBounds();
  const r2 = (x: number) => Math.round(x * 100) / 100;
  const west = r2(Math.max(-180, b.getWest()));
  const south = r2(Math.max(-90, b.getSouth()));
  const east = r2(Math.min(180, b.getEast()));
  const north = r2(Math.min(90, b.getNorth()));
  if (east <= west || north <= south) return null;
  return { west, south, east, north };
}

function sameBounds(a: Bounds | null, b: Bounds): boolean {
  return (
    a !== null &&
    a.west === b.west &&
    a.south === b.south &&
    a.east === b.east &&
    a.north === b.north
  );
}

interface Arrow {
  lon: number;
  lat: number;
  u: number;
  v: number;
}

/** Cell-center arrows, row-major from the NW corner (mirrors core `wind_grid`);
 *  masked cells (null u/v) are dropped. */
function fieldArrows(field: WindField): Arrow[] {
  const { bbox, nx, ny, u, v } = field;
  const dx = (bbox.east - bbox.west) / nx;
  const dy = (bbox.north - bbox.south) / ny;
  const arrows: Arrow[] = [];
  for (let row = 0; row < ny; row++) {
    const lat = bbox.north - (row + 0.5) * dy;
    for (let col = 0; col < nx; col++) {
      const idx = row * nx + col;
      const cu = u[idx];
      const cv = v[idx];
      if (cu === null || cu === undefined || cv === null || cv === undefined) continue;
      arrows.push({ lon: bbox.west + (col + 0.5) * dx, lat, u: cu, v: cv });
    }
  }
  return arrows;
}

function roundRectPath(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number,
): void {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

/** One arrow centered on (cx, cy), pointing along screen unit vector (sx, sy).
 *  Two passes — a dark halo then a bright stroke — so it reads on any basemap. */
function drawArrow(
  ctx: CanvasRenderingContext2D,
  cx: number,
  cy: number,
  sx: number,
  sy: number,
  len: number,
  alpha: number,
): void {
  const half = len / 2;
  const hx = cx + sx * half;
  const hy = cy + sy * half;
  const tx = cx - sx * half;
  const ty = cy - sy * half;
  const head = Math.min(6, len * 0.45);
  const angle = Math.atan2(sy, sx);
  const a1 = angle + (Math.PI * 5) / 6;
  const a2 = angle - (Math.PI * 5) / 6;

  const pass = (color: string, width: number) => {
    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    ctx.lineWidth = width;
    ctx.beginPath();
    ctx.moveTo(tx, ty);
    ctx.lineTo(hx, hy);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(hx, hy);
    ctx.lineTo(hx + head * Math.cos(a1), hy + head * Math.sin(a1));
    ctx.lineTo(hx + head * Math.cos(a2), hy + head * Math.sin(a2));
    ctx.closePath();
    ctx.fill();
  };

  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.globalAlpha = alpha * 0.55;
  pass("rgba(15, 23, 42, 0.95)", 3.4);
  ctx.globalAlpha = alpha;
  pass("rgba(255, 255, 255, 0.95)", 1.5);
  ctx.globalAlpha = 1;
}

function formatInstant(iso: string): string {
  const d = new Date(iso);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} ${p(
    d.getUTCHours(),
  )}:${p(d.getUTCMinutes())} UTC`;
}

function drawLabel(ctx: CanvasRenderingContext2D, viewW: number, field: WindField): void {
  const text = `ERA5 wind · ${formatInstant(field.when)}`;
  ctx.font = "11px system-ui, -apple-system, sans-serif";
  ctx.textBaseline = "middle";
  const padX = 8;
  const boxW = ctx.measureText(text).width + padX * 2;
  const boxH = 22;
  const x = viewW - boxW - 10;
  const y = 10;
  ctx.globalAlpha = 1;
  ctx.fillStyle = "rgba(15, 23, 42, 0.72)";
  roundRectPath(ctx, x, y, boxW, boxH, 5);
  ctx.fill();
  ctx.fillStyle = "rgba(255, 255, 255, 0.95)";
  ctx.fillText(text, x + padX, y + boxH / 2 + 0.5);
}

function drawWindField(
  map: maplibregl.Map,
  canvas: HTMLCanvasElement | null,
  field: WindField | null,
): void {
  const ctx = canvas?.getContext("2d");
  if (!canvas || !ctx) return;

  const dpr = window.devicePixelRatio || 1;
  const glCanvas = map.getCanvas();
  const viewW = glCanvas.clientWidth;
  const viewH = glCanvas.clientHeight;
  const pxW = Math.round(viewW * dpr);
  const pxH = Math.round(viewH * dpr);
  if (canvas.width !== pxW || canvas.height !== pxH) {
    canvas.width = pxW;
    canvas.height = pxH;
    canvas.style.width = `${viewW}px`;
    canvas.style.height = `${viewH}px`;
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, viewW, viewH);
  if (!field) return;

  for (const arrow of fieldArrows(field)) {
    const p = map.project([arrow.lon, arrow.lat]);
    if (p.x < -20 || p.y < -20 || p.x > viewW + 20 || p.y > viewH + 20) continue;
    const speed = Math.hypot(arrow.u, arrow.v);
    const len = clamp(MIN_LEN + speed * SPEED_TO_PX, MIN_LEN, MAX_LEN);
    const alpha = clamp(0.4 + speed / 18, 0.4, 0.9);
    const inv = speed > 1e-6 ? 1 / speed : 0;
    // North is up on screen, so the screen y-component is -v.
    drawArrow(ctx, p.x, p.y, arrow.u * inv, -arrow.v * inv, len, alpha);
  }
  drawLabel(ctx, viewW, field);
}

export function WindOverlay() {
  const { map, ready } = useMapContext();
  const enabled = useWindStore((s) => s.enabled);
  const mode = useDateStore((s) => s.mode);
  const end = useDateStore((s) => s.end);
  const targetDate = useDateStore((s) => s.targetDate);
  const timeIso = `${mode === "single" ? targetDate : end}T12:00:00Z`;

  const [bounds, setBounds] = useState<Bounds | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  // Follow the viewport: refetch when the 2 dp bounds actually change.
  useEffect(() => {
    if (!map || !ready) return;
    const update = () => {
      const next = viewportBounds(map);
      if (next) setBounds((prev) => (sameBounds(prev, next) ? prev : next));
    };
    update();
    map.on("moveend", update);
    return () => {
      map.off("moveend", update);
    };
  }, [map, ready]);

  const query = useQuery({
    queryKey: ["wind-field", bounds, timeIso],
    queryFn: () => fetchWindField({ ...bounds!, time: timeIso, nx: 24 }),
    enabled: enabled && bounds !== null,
    staleTime: 5 * 60_000,
  });

  const field = enabled ? (query.data ?? null) : null;

  // The overlay canvas: created once, layered over the map's WebGL canvas.
  useEffect(() => {
    if (!map || !ready) return;
    const canvas = document.createElement("canvas");
    canvas.className = "wind-overlay-canvas";
    map.getCanvasContainer().appendChild(canvas);
    canvasRef.current = canvas;
    return () => {
      canvas.remove();
      canvasRef.current = null;
    };
  }, [map, ready]);

  // Redraw as the map moves (arrows stay pinned) and whenever the field changes.
  useEffect(() => {
    if (!map || !ready) return;
    const draw = () => drawWindField(map, canvasRef.current, field);
    draw();
    map.on("move", draw);
    map.on("resize", draw);
    return () => {
      map.off("move", draw);
      map.off("resize", draw);
    };
  }, [map, ready, field]);

  return null;
}
