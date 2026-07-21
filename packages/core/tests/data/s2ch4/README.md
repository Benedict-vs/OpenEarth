# S2CH4 benchmark fixtures

Three **unmodified** files from the S2CH4 simulated-plume dataset, committed as
offline test fixtures for `scripts/s2ch4_benchmark.py` and its reader tests.

- **Source:** Gorroño, J. et al. (2023), *A benchmark dataset for methane
  point-source quantification using Sentinel-2 imagery*, AMT 16, 89.
  Harvard Dataverse, **doi:10.7910/DVN/KRNPEH**, dataset version 2.
- **License:** **CC0 1.0** (public domain dedication) — committing these
  originals is permitted and intended. (Contrast the CH4Net wall: gated,
  CC-BY-NC-ND, nothing derived ever committed.)
- **Retrieved:** 2026-07-21, via the Dataverse native access API
  (`/api/access/datafile/{id}`), MD5-verified against the dataset JSON.

The three files are the **Hassi Messaoud** base scene (tile T32SKA, acquired
2021-07-02, Sentinel-2A), plume shape 0, at three flux levels:

| File tag | True flux |
| -------- | --------- |
| `…_plume0_Q0`     | 0 kg/h (plume-free reference) |
| `…_plume0_Q5000`  | 5 000 kg/h (5 t/h) |
| `…_plume0_Q50000` | 50 000 kg/h (50 t/h) |

Each is a netCDF4/HDF5 file (`S2TOA` (75,75,13) float64 TOA reflectance in L1C
band order, scalars `SZA`/`VZA`/`U10`, `lat`/`lon` (75,75), `xch4` (75,75) truth
column enhancement). The full ~925 MB dataset is fetched separately by
`scripts/fetch_s2ch4.py` into a git-ignored `<data_dir>/s2ch4/`.
