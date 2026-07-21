#!/usr/bin/env python
"""Download the S2CH4 simulated-plume benchmark dataset (Gorroño et al. 2023).

Harvard Dataverse ``doi:10.7910/DVN/KRNPEH`` (version 2, **CC0 1.0** — public
domain), 1345 netCDF4/HDF5 files (~0.7 MB each, ~925 MB total): three real
Sentinel-2A L1C base scenes with WRF-LES methane plumes forward-modelled onto
them at known flux. This is the ground-truth instrument for the offline
benchmark (``scripts/s2ch4_benchmark.py``).

    uv run python scripts/fetch_s2ch4.py --site hassi --max-files 20   # subset
    uv run python scripts/fetch_s2ch4.py                               # full ~925 MB

Files land under ``<data_dir>/s2ch4/`` (git-ignored). Each file is verified
against the dataset's published MD5; already-verified files are skipped, so the
run is idempotent and resumable. Only the Dataverse *native API* and stdlib
``urllib`` are used — no new runtime dependency, and no auth (the data is CC0).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from openearth.settings import get_settings

# Dataverse native API. The dataset JSON lists every file with its id + MD5; the
# access endpoint 302-redirects to storage (urllib follows it).
_DATASET_DOI = "doi:10.7910/DVN/KRNPEH"
_DATASET_JSON_URL = (
    "https://dataverse.harvard.edu/api/datasets/:persistentId?persistentId=" + _DATASET_DOI
)
_ACCESS_URL = "https://dataverse.harvard.edu/api/access/datafile/{id}"

# The benchmark's three base scenes, keyed by the CLI site name → MGRS tile. One
# acquisition date per site (see the s2ch4_benchmark header facts).
SITE_TILES = {"hassi": "32SKA", "permian": "13SGR", "korpeje": "40SBH"}

_DOWNLOAD_TIMEOUT_S = 120
_MAX_ATTEMPTS = 3
# Dataverse rejects the default Python-urllib User-Agent with 403; send a plain one.
_USER_AGENT = "openearth-s2ch4-fetch/1.0"


def _get(url: str) -> bytes:
    """GET *url* (following redirects) with a UA Dataverse accepts."""
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=_DOWNLOAD_TIMEOUT_S) as resp:
        return resp.read()  # type: ignore[no-any-return]


@dataclass(frozen=True)
class FileEntry:
    """One dataset file's download coordinates."""

    file_id: int
    md5: str
    label: str
    tile: str


def _fetch_dataset_json() -> dict[str, object]:
    return json.loads(_get(_DATASET_JSON_URL).decode("utf-8"))  # type: ignore[no-any-return]


def _list_files(site: str | None) -> list[FileEntry]:
    """The dataset's file entries, optionally filtered to one site's tile."""
    data = _fetch_dataset_json()
    version = data["data"]["latestVersion"]  # type: ignore[index,call-overload]
    entries: list[FileEntry] = []
    for f in version["files"]:
        label = str(f["label"])
        df = f["dataFile"]
        tile = next((t for t in SITE_TILES.values() if f"_T{t}_" in label), "")
        entries.append(FileEntry(file_id=int(df["id"]), md5=str(df["md5"]), label=label, tile=tile))
    if site is not None:
        want = SITE_TILES[site]
        entries = [e for e in entries if e.tile == want]
    entries.sort(key=lambda e: e.label)
    return entries


def _md5(path: Path) -> str:
    # Dataverse publishes MD5s; this is an integrity check, not a security hash.
    h = hashlib.md5()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _download_one(entry: FileEntry, dest: Path) -> None:
    """Fetch *entry* to *dest* (atomic), verifying the published MD5."""
    url = _ACCESS_URL.format(id=entry.file_id)
    tmp = dest.with_name(dest.name + ".part")
    last_exc: Exception | None = None
    for _ in range(_MAX_ATTEMPTS):
        try:
            tmp.write_bytes(_get(url))
            got = _md5(tmp)
            if got != entry.md5:
                tmp.unlink(missing_ok=True)
                raise ValueError(f"MD5 mismatch for {entry.label}: got {got}, want {entry.md5}")
            tmp.rename(dest)
            return
        except (urllib.error.URLError, ValueError, TimeoutError) as exc:
            last_exc = exc
    tmp.unlink(missing_ok=True)
    raise RuntimeError(
        f"failed to download {entry.label} after {_MAX_ATTEMPTS} attempts: {last_exc}"
    )


def fetch(site: str | None, max_files: int | None, out_dir: Path) -> dict[str, int]:
    """Download (or verify) the dataset subset into *out_dir*; return a summary."""
    out_dir.mkdir(parents=True, exist_ok=True)
    entries = _list_files(site)
    if max_files is not None:
        entries = entries[:max_files]

    downloaded = skipped = failed = 0
    for i, entry in enumerate(entries, 1):
        dest = out_dir / entry.label
        if dest.exists() and _md5(dest) == entry.md5:
            skipped += 1
            continue
        try:
            _download_one(entry, dest)
            downloaded += 1
        except RuntimeError as exc:
            failed += 1
            print(f"  ! {exc}", file=sys.stderr)
        if i % 50 == 0 or i == len(entries):
            print(
                f"  [{i}/{len(entries)}] downloaded={downloaded} skipped={skipped} failed={failed}"
            )
    return {"total": len(entries), "downloaded": downloaded, "skipped": skipped, "failed": failed}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", choices=sorted(SITE_TILES), help="restrict to one base scene")
    parser.add_argument("--max-files", type=int, default=None, help="cap the number of files")
    parser.add_argument(
        "--data-dir", type=Path, default=None, help="override the settings data_dir"
    )
    args = parser.parse_args()

    data_dir = args.data_dir if args.data_dir is not None else get_settings().data_dir
    out_dir = data_dir / "s2ch4"
    site_note = args.site or "all sites"
    print(f"S2CH4 {site_note} → {out_dir}  (DOI {_DATASET_DOI}, CC0 1.0)")

    summary = fetch(args.site, args.max_files, out_dir)
    print(
        f"\ndone: {summary['downloaded']} downloaded, {summary['skipped']} already verified, "
        f"{summary['failed']} failed ({summary['total']} in scope)"
    )
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
