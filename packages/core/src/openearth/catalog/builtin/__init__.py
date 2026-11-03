"""Built-in curated datasets (ported from the v1 registries)."""

from openearth.catalog.builtin.emit import EMIT_DATASET, EMIT_PRODUCTS
from openearth.catalog.builtin.hls import HLS_DATASET, HLS_PRODUCTS
from openearth.catalog.builtin.landsat import LANDSAT_DATASET, LANDSAT_PRODUCTS
from openearth.catalog.builtin.s1 import S1_DATASET, S1_PRODUCTS
from openearth.catalog.builtin.s2 import METHANE_S2_KEYS, S2_DATASET, S2_PRODUCTS
from openearth.catalog.builtin.s5p import S5P_DATASET, S5P_PRODUCTS

__all__ = [
    "EMIT_DATASET",
    "EMIT_PRODUCTS",
    "HLS_DATASET",
    "HLS_PRODUCTS",
    "LANDSAT_DATASET",
    "LANDSAT_PRODUCTS",
    "METHANE_S2_KEYS",
    "S1_DATASET",
    "S1_PRODUCTS",
    "S2_DATASET",
    "S2_PRODUCTS",
    "S5P_DATASET",
    "S5P_PRODUCTS",
]
