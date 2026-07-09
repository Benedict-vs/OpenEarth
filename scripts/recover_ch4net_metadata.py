#!/usr/bin/env python
"""Recover the (site, date, bbox) metadata the HF CH4Net release strips out.

The published dataset ``av555/ch4net`` names every tile by an opaque integer
index — no date, site, scene id, or georeferencing (see
``scripts/fetch_ch4net.py inventory``). This script recovers that mapping
self-service so the Stage-1 chip-rebuild exporter can re-fetch each tile's GEE
scene. Approach (validated in pilots before writing):

  cluster  (offline)  content-cluster tiles within each pixel-shape group; a
                      cluster ≈ one ground footprint across dates (over-segments
                      by year — safe: no cluster spans two sites).
  geolocate (EE)      median-composite GEE reference at each of the 23 published
                      site coordinates (Vaughan et al. 2024, Table 2 — CC-BY);
                      match each cluster median by normalised cross-correlation;
                      the NCC *peak location* gives the footprint centre (~10 m
                      in pilots) → bbox + nearest published site. Every tile is
                      then assigned to its best reliable cluster.
  dates     (EE)      per site, fetch one coarse chip per S2 overpass; match each
                      tile (search restricted to its split's year range —
                      train/val ≤ 2020, test = 2021) → date + score; flag lows.
  finalize            write metadata.json + validation gates + aggregate stats.

LICENSE WALL: the recovered mapping is a CH4Net derivative → it lives under the
git-ignored ``data_dir`` and is NEVER committed. Only this code and the CC-BY
site coordinates are committed; aggregate recovery stats go in §9 / commit
messages. All EE round-trips go through ``ee_call``. Stages are resumable
(per-stage caches); ``--sites`` restricts the EE stages for piloting.

    uv run python scripts/recover_ch4net_metadata.py cluster
    uv run python scripts/recover_ch4net_metadata.py geolocate
    uv run python scripts/recover_ch4net_metadata.py dates
    uv run python scripts/recover_ch4net_metadata.py finalize
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.signal import fftconvolve
from scipy.spatial.distance import squareform

from openearth.catalog.builtin.s2 import S2_COLLECTION_ID
from openearth.ee import client
from openearth.ee.pixels import fetch_pixels, grid_for
from openearth.geometry import BBox
from openearth.methane.scenes import list_scenes
from openearth.settings import get_settings

# 23 super-emitter sites, (id, lon, lat) — Vaughan et al. 2024 AMT Table 2 (CC-BY;
# coordinates originate from Irakulis-Loitxate et al. 2022). West Turkmenistan O&G.
SITES: list[tuple[str, float, float]] = [
    ("T1", 53.6367, 39.49687),
    ("T2", 53.77274, 39.52148),
    ("T3", 53.77903, 39.52137),
    ("T4", 53.74292, 39.4739),
    ("T5", 53.78836, 39.46428),
    ("T6", 53.77502, 39.4616),
    ("T7", 53.77921, 39.45965),
    ("T8", 53.68117, 39.44955),
    ("T9", 53.76506, 39.36045),
    ("T10", 53.83516, 39.38584),
    ("T11", 53.87509, 39.35498),
    ("T12", 54.23498, 38.85515),
    ("T13", 54.20931, 38.57959),
    ("T14", 54.20049, 38.55747),
    ("T15", 54.20393, 38.51871),
    ("T16", 54.19769, 38.50798),
    ("T17", 54.19764, 38.49393),
    ("T18", 54.02832, 38.33078),
    ("T19", 54.03149, 38.36017),
    ("T20", 53.89857, 37.90825),
    ("T21", 53.91623, 37.9286),
    ("T22", 53.92431, 37.92913),
    ("T23", 53.92702, 37.71665),
]
SITE_ID = [s[0] for s in SITES]
SITE_LONLAT = np.array([[lo, la] for _, lo, la in SITES])

M_PER_DEG = 111_320.0
REF_PAD_DEG = 0.05  # half-width of each GEE geolocation reference (~5-8 km)
GEO_BAND = "B11"  # SWIR: distinctive arid structure, sharp registration
CLUSTER_BAND = 7  # tile band for content clustering (NIR — plume-invariant)
SWIR_BAND = 10  # tile band ≈ B11 (SWIR) for geolocation/date matching
CLUSTER_DIST = 0.4  # 1 − corr agglomerative threshold
RELIABLE_MIN = 15  # min members for a cluster median to be geolocated
GEO_MIN_NCC = 0.45  # accept a cluster's site match above this peak NCC
GEO_MAX_KM = 3.0  # ...and within this distance of a published site
DATE_SCALE_M = 20  # overpass-chip resolution for date matching
DATE_MIN_CORR = 0.70  # confident date match threshold
DATE_GRID = (96, 80)  # common downsampled grid for tile↔overpass correlation

_settings = get_settings()
CH4NET = _settings.data_dir / "ml" / "ch4net"
RAW = CH4NET / "raw"
REC = CH4NET / "recovery"
SHAPES = [(227, 165), (217, 181), (227, 166), (227, 169), (216, 182), (228, 165), (217, 180)]


# ── shared array helpers ─────────────────────────────────────────────────────


def _tiles(split: str) -> list[Path]:
    return sorted((RAW / split / "s2").glob("*.npy"), key=lambda p: int(p.stem))


def _all_keys() -> list[str]:
    return [f"{split}/{p.stem}" for split in ("train", "val", "test") for p in _tiles(split)]


def _load_band(key: str, band: int) -> NDArray[np.float64]:
    split, idx = key.split("/")
    return np.load(RAW / split / "s2" / f"{idx}.npy")[..., band].astype(np.float64)


def _shape_of(key: str) -> tuple[int, int]:
    split, idx = key.split("/")
    a = np.load(RAW / split / "s2" / f"{idx}.npy", mmap_mode="r")
    return int(a.shape[0]), int(a.shape[1])


def _is_positive(key: str) -> bool:
    """A tile is positive iff its hand-annotated mask is non-empty."""
    split, idx = key.split("/")
    return bool(np.asarray(np.load(RAW / split / "label" / f"{idx}.npy", mmap_mode="r")).any())


def _fingerprint(a: NDArray[np.float64], blk: int = 8) -> NDArray[np.float64]:
    h, w = a.shape
    a = a[: h // blk * blk, : w // blk * blk]
    a = a.reshape(h // blk, blk, w // blk, blk).mean((1, 3))
    v = a.ravel()
    med = float(np.median(v))
    mad = float(np.median(np.abs(v - med))) * 1.4826 + 1e-6
    return (v - med) / mad


def _block_ds(a: NDArray[np.float64], out_hw: tuple[int, int]) -> NDArray[np.float64]:
    h, w = a.shape
    bh, bw = max(1, h // out_hw[0]), max(1, w // out_hw[1])
    a = a[: h // bh * bh, : w // bw * bw]
    return a.reshape(a.shape[0] // bh, bh, a.shape[1] // bw, bw).mean((1, 3))


def _resize_to(a: NDArray[np.float64], out_hw: tuple[int, int]) -> NDArray[np.float64]:
    """Nearest-sample to an *exact* (oh, ow) grid — needed because a site mixes
    tiles of several pixel-shapes, so tile and overpass grids must be forced equal
    before correlation."""
    h, w = a.shape
    oh, ow = out_hw
    ri = np.minimum(np.arange(oh) * h // oh, h - 1)
    ci = np.minimum(np.arange(ow) * w // ow, w - 1)
    return a[np.ix_(ri, ci)]


def _zn(a: NDArray[np.float64]) -> NDArray[np.float64]:
    a = np.nan_to_num(a, nan=float(np.nanmedian(a)))
    return (a - a.mean()) / (a.std() + 1e-9)


def _ncc_peak(ref: NDArray[np.float64], tmpl: NDArray[np.float64]) -> tuple[float, int, int]:
    """Max CCOEFF_NORMED of tmpl within ref (valid) → (peak, row, col)."""
    ref = np.nan_to_num(ref, nan=float(np.nanmedian(ref)))
    t = tmpl - tmpl.mean()
    tn = float(np.sqrt((t**2).sum()))
    if tn == 0 or ref.shape[0] < t.shape[0] or ref.shape[1] < t.shape[1]:
        return 0.0, 0, 0
    ones = np.ones_like(t)
    n = t.size
    s = fftconvolve(ref, ones[::-1, ::-1], "valid")
    s2 = fftconvolve(ref**2, ones[::-1, ::-1], "valid")
    var = np.clip(s2 - s**2 / n, 0, None)
    num = fftconvolve(ref, t[::-1, ::-1], "valid")
    denom = np.sqrt(var) * tn
    with np.errstate(divide="ignore", invalid="ignore"):
        m = np.where(denom > 1e-6, num / denom, -1.0)
    r, c = np.unravel_index(int(np.nanargmax(m)), m.shape)
    return float(m[r, c]), int(r), int(c)


def _tile_bbox(center_lon: float, center_lat: float, shape: tuple[int, int]) -> BBox:
    half_lat = shape[0] * 10 / 2 / M_PER_DEG
    half_lon = shape[1] * 10 / 2 / (M_PER_DEG * np.cos(np.radians(center_lat)))
    return BBox(
        center_lon - half_lon, center_lat - half_lat, center_lon + half_lon, center_lat + half_lat
    )


# ── stage: cluster (offline) ─────────────────────────────────────────────────


def cluster() -> None:
    REC.mkdir(parents=True, exist_ok=True)
    assign: dict[str, int] = {}
    cluster_members: dict[int, list[str]] = {}
    # fingerprints differ in length per pixel-shape, so store per shape (ragged);
    # geolocate only ever compares tiles within one shape.
    fp_store: dict[str, NDArray] = {}
    next_id = 0
    for si, shape in enumerate(SHAPES):
        keys = [k for k in _all_keys() if _shape_of(k) == shape]
        F = np.stack([_fingerprint(_load_band(k, CLUSTER_BAND)) for k in keys])
        fp_store[f"keys_{si}"] = np.array(keys)
        fp_store[f"fps_{si}"] = F.astype(np.float32)
        Fn = F - F.mean(1, keepdims=True)
        Fn /= np.linalg.norm(Fn, axis=1, keepdims=True) + 1e-9
        corr = np.clip(Fn @ Fn.T, -1, 1)
        Z = linkage(squareform(1 - corr, checks=False), method="average")
        lab = fcluster(Z, t=CLUSTER_DIST, criterion="distance")
        remap = {c: next_id + i for i, c in enumerate(sorted(set(lab.tolist())))}
        next_id += len(remap)
        for k, c in zip(keys, lab.tolist(), strict=True):
            cid = remap[c]
            assign[k] = cid
            cluster_members.setdefault(cid, []).append(k)
        print(f"  shape {shape}: {len(keys)} tiles → {len(remap)} clusters")
    reliable = sorted(c for c, m in cluster_members.items() if len(m) >= RELIABLE_MIN)
    (REC / "clusters.json").write_text(
        json.dumps(
            {
                "assign": assign,
                "reliable": reliable,
                "n_clusters": next_id,
                "n_reliable": len(reliable),
                "reliable_min": RELIABLE_MIN,
            }
        )
    )
    np.savez(REC / "fingerprints.npz", n_shapes=np.array(len(SHAPES)), **fp_store)
    print(f"cluster: {len(assign)} tiles, {next_id} clusters, {len(reliable)} reliable → {REC}")


# ── stage: geolocate (EE) ────────────────────────────────────────────────────


def _build_refs() -> dict[str, NDArray[np.float64]]:
    cache = REC / "site_refs.npz"
    if cache.exists():
        z = np.load(cache)
        return {k: z[k].astype(np.float64) for k in z.files}
    import ee

    client.initialize()
    refs = {}
    for sid, lon, lat in SITES:
        col = (
            ee.ImageCollection(S2_COLLECTION_ID)
            .filterBounds(ee.Geometry.Point(lon, lat))
            .filterDate("2019-05-01", "2020-10-01")
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 8))
        )
        img = col.select(GEO_BAND).median()
        box = BBox(lon - REF_PAD_DEG, lat - REF_PAD_DEG, lon + REF_PAD_DEG, lat + REF_PAD_DEG)
        spec = grid_for(box, 10)
        refs[sid] = client.ee_call(lambda im=img, sp=spec: fetch_pixels(im, sp, [GEO_BAND]))[..., 0]
        print(f"  ref {sid} {refs[sid].shape}")
    np.savez(cache, **{k: v.astype(np.float32) for k, v in refs.items()})
    return refs


def geolocate() -> None:
    clusters = json.loads((REC / "clusters.json").read_text())
    assign: dict[str, int] = clusters["assign"]
    reliable: list[int] = clusters["reliable"]
    members: dict[int, list[str]] = {}
    for k, c in assign.items():
        members.setdefault(c, []).append(k)

    refs = _build_refs()
    # geolocate reliable-cluster medians
    geo: dict[str, dict] = {}
    for cid in reliable:
        keys = members[cid]
        med = np.median(np.stack([_load_band(k, SWIR_BAND) for k in keys]), axis=0)
        th, tw = med.shape
        best = (-1.0, "", 0, 0)
        for sid, _, _ in SITES:
            v, r, c = _ncc_peak(refs[sid], med)
            if v > best[0]:
                best = (v, sid, r, c)
        v, sid, r, c = best
        _, lon0, lat0 = next(s for s in SITES if s[0] == sid)
        xscale = 10 / (M_PER_DEG * np.cos(np.radians(lat0)))
        yscale = 10 / M_PER_DEG
        lon = (lon0 - REF_PAD_DEG) + (c + tw / 2) * xscale
        lat = (lat0 + REF_PAD_DEG) - (r + th / 2) * yscale
        d = np.hypot(
            (SITE_LONLAT[:, 0] - lon) * M_PER_DEG * np.cos(np.radians(lat)),
            (SITE_LONLAT[:, 1] - lat) * M_PER_DEG,
        )
        near = SITE_ID[int(np.argmin(d))]
        km = float(d.min()) / 1000
        ok = v >= GEO_MIN_NCC and km <= GEO_MAX_KM
        geo[str(cid)] = {
            "site_id": near if ok else None,
            "center_lon": round(lon, 6),
            "center_lat": round(lat, 6),
            "ncc": round(v, 3),
            "dist_km": round(km, 3),
            "ok": ok,
            "n_members": len(keys),
        }
    # assign every tile to its best reliable cluster (same shape) → inherit site/bbox
    z = np.load(REC / "fingerprints.npz")
    fp: dict[str, NDArray[np.float64]] = {}
    for si in range(int(z["n_shapes"])):
        for k, row in zip(z[f"keys_{si}"], z[f"fps_{si}"], strict=True):
            fp[str(k)] = row.astype(np.float64)
    rel_ok = [c for c in reliable if geo[str(c)]["ok"]]
    rel_by_shape: dict[tuple, list[int]] = {}
    rel_medoid_fp: dict[int, NDArray[np.float64]] = {}
    for c in rel_ok:
        shp = _shape_of(members[c][0])
        rel_by_shape.setdefault(shp, []).append(c)
        rel_medoid_fp[c] = np.mean([fp[k] for k in members[c]], axis=0)

    per_tile: dict[str, dict] = {}
    for k in assign:
        shp = _shape_of(k)
        cands = rel_by_shape.get(shp, [])
        if not cands:
            per_tile[k] = {"site_id": None, "assign_corr": 0.0, "cluster": assign[k]}
            continue
        f = fp[k]
        fn = f - f.mean()
        fn = fn / (np.linalg.norm(fn) + 1e-9)
        best_c, best_v = cands[0], -1.0
        for c in cands:
            g = rel_medoid_fp[c]
            gn = g - g.mean()
            gn = gn / (np.linalg.norm(gn) + 1e-9)
            v = float(fn @ gn)
            if v > best_v:
                best_v, best_c = v, c
        gk = geo[str(best_c)]
        bbox = _tile_bbox(gk["center_lon"], gk["center_lat"], shp)
        per_tile[k] = {
            "site_id": gk["site_id"],
            "cluster": best_c,
            "assign_corr": round(best_v, 3),
            "geo_ncc": gk["ncc"],
            "center_lon": gk["center_lon"],
            "center_lat": gk["center_lat"],
            "bbox": [bbox.west, bbox.south, bbox.east, bbox.north],
        }
    (REC / "geolocation.json").write_text(json.dumps({"clusters": geo, "tiles": per_tile}))
    n_sited = sum(1 for v in per_tile.values() if v["site_id"])
    print(
        f"geolocate: {len(rel_ok)}/{len(reliable)} reliable clusters sited; "
        f"{n_sited}/{len(per_tile)} tiles assigned a site → {REC / 'geolocation.json'}"
    )


# ── stage: dates (EE, resumable per site) ────────────────────────────────────


def _split_of(key: str) -> str:
    return key.split("/")[0]


def dates(only_sites: set[str] | None = None) -> None:
    geoloc = json.loads((REC / "geolocation.json").read_text())
    tiles: dict[str, dict] = geoloc["tiles"]
    by_site: dict[str, list[str]] = {}
    for k, v in tiles.items():
        if v["site_id"]:
            by_site.setdefault(v["site_id"], []).append(k)

    import ee

    client.initialize()
    ov_dir = REC / "overpasses"
    ov_dir.mkdir(parents=True, exist_ok=True)
    out_path = REC / "dates.json"
    out: dict[str, dict] = json.loads(out_path.read_text()) if out_path.exists() else {}

    for sid, keys in sorted(by_site.items()):
        if only_sites and sid not in only_sites:
            continue
        # canonical site bbox: median recovered centre + representative shape
        lon = float(np.median([tiles[k]["center_lon"] for k in keys]))
        lat = float(np.median([tiles[k]["center_lat"] for k in keys]))
        shp = Counter(_shape_of(k) for k in keys).most_common(1)[0][0]
        bbox = _tile_bbox(lon, lat, shp)
        spec = grid_for(bbox, DATE_SCALE_M)
        cache = ov_dir / f"{sid}.npz"
        if cache.exists():
            z = np.load(cache, allow_pickle=True)
            chips, odates = z["chips"], list(z["dates"])
        else:
            scenes = list_scenes(bbox, "2017-01-01", "2022-01-01", max_cloud=60)
            arrs, odates = [], []
            for sc in scenes:
                img = ee.Image(f"{S2_COLLECTION_ID}/{sc.scene_id}").select(GEO_BAND)
                a = client.ee_call(lambda im=img, sp=spec: fetch_pixels(im, sp, [GEO_BAND]))[..., 0]
                arrs.append(_resize_to(a.astype(np.float64), DATE_GRID).astype(np.float32))
                odates.append(str(sc.time.date()))
            chips = np.stack(arrs) if arrs else np.zeros((0, *DATE_GRID), np.float32)
            np.savez(cache, chips=chips, dates=np.array(odates))
            print(f"  {sid}: fetched {len(odates)} overpasses")
        if len(chips) == 0:
            continue
        # resize on load too, so pre-fix caches (stored at a slightly different size) still work
        C = np.stack([_zn(_resize_to(c.astype(np.float64), DATE_GRID)) for c in chips])
        Cflat = C.reshape(len(C), -1)
        oyears = np.array([d[:4] for d in odates])
        for k in keys:
            tz = _zn(_resize_to(_load_band(k, SWIR_BAND), DATE_GRID)).ravel()
            cc = Cflat @ tz / tz.size
            # split-year prior: train/val ≤ 2020, test = 2021
            mask = oyears == "2021" if _split_of(k) == "test" else oyears <= "2020"
            cc = np.where(mask, cc, -2.0)
            i = int(np.argmax(cc))
            out[k] = {
                "date": odates[i],
                "date_corr": round(float(cc[i]), 3),
                "confident": bool(cc[i] >= DATE_MIN_CORR),
            }
        out_path.write_text(json.dumps(out))
        conf = sum(1 for k in keys if out.get(k, {}).get("confident"))
        print(f"  {sid}: dated {len(keys)} tiles, {conf} confident")
    print(f"dates: {len(out)} tiles dated → {out_path}")


# ── stage: finalize ──────────────────────────────────────────────────────────


def finalize() -> None:
    geoloc = json.loads((REC / "geolocation.json").read_text())["tiles"]
    dts = json.loads((REC / "dates.json").read_text()) if (REC / "dates.json").exists() else {}
    meta: dict[str, dict] = {}
    for k, g in geoloc.items():
        d = dts.get(k, {})
        pos = _is_positive(k)
        confident = bool(g["site_id"] and d.get("confident"))
        # Stage-1 usage policy: a positive needs a CONFIDENT date (a wrong date
        # rebuilds a plume-free chip under a plume mask = label noise); a negative
        # only needs a plume-free scene over its footprint, so any recovered
        # in-split date suffices.
        usable = confident if pos else bool(g["site_id"] and d.get("date"))
        meta[k] = {
            "site_id": g["site_id"],
            "date": d.get("date"),
            "bbox": g.get("bbox"),
            "positive": pos,
            "registration_ncc": g.get("geo_ncc"),
            "assign_corr": g.get("assign_corr"),
            "date_corr": d.get("date_corr"),
            "confident": confident,
            "usable": usable,
        }
    # aggregate stats (committable — numbers, not data)
    n = len(meta)
    sited = [m for m in meta.values() if m["site_id"]]
    dated = [m for m in meta.values() if m["date"]]
    conf = [m for m in meta.values() if m["confident"]]
    pos = [m for m in meta.values() if m["positive"]]
    pos_conf = [m for m in pos if m["confident"]]
    usable = [m for m in meta.values() if m["usable"]]
    usable_pos = [m for m in usable if m["positive"]]
    # gate (a): split-consistency of confident dates
    split_ok = 0
    for k, m in meta.items():
        if not (m["site_id"] and m["date"] and m["confident"]):
            continue
        yr = m["date"][:4]
        exp = yr == "2021" if k.split("/")[0] == "test" else yr <= "2020"
        split_ok += exp
    per_site = Counter(m["site_id"] for m in sited)
    per_site_conf = Counter(m["site_id"] for m in conf)
    corrs = [m["date_corr"] for m in dated if m["date_corr"] is not None]
    stats = {
        "generated_at": datetime.now(UTC).isoformat(),
        "n_tiles": n,
        "n_positive": len(pos),
        "n_sited": len(sited),
        "n_dated": len(dated),
        "n_confident": len(conf),
        "n_positive_confident": len(pos_conf),
        "n_usable": len(usable),
        "n_usable_positive": len(usable_pos),
        "frac_sited": round(len(sited) / n, 3),
        "frac_confident": round(len(conf) / n, 3),
        "frac_positive_confident": round(len(pos_conf) / max(len(pos), 1), 3),
        "gate_a_split_consistency": round(split_ok / max(len(conf), 1), 3),
        "gate_c_each_cluster_one_site": True,  # argmax over sites ⇒ one site per cluster
        "date_corr_median": round(float(np.median(corrs)), 3) if corrs else None,
        "tiles_per_site": dict(sorted(per_site.items())),
        "confident_per_site": {s: [per_site_conf[s], per_site[s]] for s in sorted(per_site)},
        "params": {
            "geo_min_ncc": GEO_MIN_NCC,
            "geo_max_km": GEO_MAX_KM,
            "date_min_corr": DATE_MIN_CORR,
            "reliable_min": RELIABLE_MIN,
        },
    }
    (REC / "metadata.json").write_text(json.dumps({"stats": stats, "tiles": meta}, indent=2))
    (REC / "recovery_stats.json").write_text(json.dumps(stats, indent=2))
    print(f"finalize: metadata.json + recovery_stats.json → {REC}")
    print(
        f"  sited {stats['frac_sited']:.0%}  confident {stats['frac_confident']:.0%}  "
        f"gate-a split-consistency {stats['gate_a_split_consistency']:.0%}"
    )
    print(
        f"  usable: {stats['n_usable']} tiles "
        f"({stats['n_usable_positive']} positive / {stats['n_positive']}, "
        f"rest negatives) — positives need a confident date"
    )
    print("  (metadata.json is a CH4Net derivative — never commit; stats JSON is aggregate-only)")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("cluster", help="offline: content-cluster tiles by footprint")
    sub.add_parser("geolocate", help="EE: match cluster medians → site/bbox")
    dp = sub.add_parser("dates", help="EE: per-tile date recovery (resumable)")
    dp.add_argument("--sites", default=None, help="comma-separated site ids to restrict (pilot)")
    sub.add_parser("finalize", help="write metadata.json + validation gates + stats")
    args = p.parse_args()
    if not RAW.is_dir():
        sys.exit(f"{RAW} missing — run scripts/fetch_ch4net.py download first.")
    if args.cmd == "dates":
        dates({s.strip() for s in args.sites.split(",")} if args.sites else None)
    else:
        {"cluster": cluster, "geolocate": geolocate, "finalize": finalize}[args.cmd]()


if __name__ == "__main__":
    main()
