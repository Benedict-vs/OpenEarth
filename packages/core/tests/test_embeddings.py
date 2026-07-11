"""AlphaEarth embeddings — pure constants + vis config (offline).

The EE chains (mosaic/seed/similarity/change/cluster) are covered by a manual live
check; here we pin the parts that must never drift without a deliberate change."""

from __future__ import annotations

import re

from openearth import embeddings as emb

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def test_band_list_is_64_unit_norm_dims() -> None:
    assert len(emb.BANDS) == 64
    assert emb.BANDS[0] == "A00"
    assert emb.BANDS[-1] == "A63"
    assert len(set(emb.BANDS)) == 64


def test_attribution_verbatim() -> None:
    # CC-BY 4.0 requires this exact string wherever a layer is shown.
    assert emb.ATTRIBUTION == (
        "The AlphaEarth Foundations Satellite Embedding dataset is produced by "
        "Google and Google DeepMind."
    )


def test_vis_ranges_ordered() -> None:
    assert emb.SIMILARITY_VIS == (-0.2, 1.0)
    assert emb.CHANGE_VIS == (0.0, 1.0)
    for lo, hi in (emb.SIMILARITY_VIS, emb.CHANGE_VIS):
        assert lo < hi


def test_palettes_valid_hex() -> None:
    for palette in (emb.SIMILARITY_PALETTE, emb.CHANGE_PALETTE, emb.CLUSTER_PALETTE):
        assert palette, "palette must be non-empty"
        for color in palette:
            assert _HEX.match(color), f"bad palette color {color!r}"


def test_cluster_palette_covers_k_max() -> None:
    # The cluster palette is cycled/truncated to k; it must have ≥ K_MAX distinct colors.
    assert emb.K_MIN == 2
    assert emb.K_MAX == 12
    assert len(emb.CLUSTER_PALETTE) >= emb.K_MAX
    assert len(set(emb.CLUSTER_PALETTE)) == len(emb.CLUSTER_PALETTE)


def test_collection_id_pinned() -> None:
    assert emb.COLLECTION == "GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL"
