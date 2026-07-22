#!/usr/bin/env python
"""Phase 10 Stage 0 spike: does Earth Engine's getThumbURL serve a 4K frame?

Decision 9 (native-locked resolution, raising ``max_dim`` 1920 → 3840) is gated
on this: ``getThumbURL`` has NO documented dimension cap, but 4K (3840×2160 ≈
8.3 MP PNG) is UNPROVEN. This mints + fetches one 3840×2160 S2 RGB thumb over a
Richmond Park bbox through the exact production ``thumb_url`` path and records
success / latency / bytes — or the exact EE refusal. A 1920 control frame is
minted first for a latency/size baseline.

Live EE, run manually with real auth (never in CI):

    uv run python scripts/spike_4k_thumb.py

The result is pasted into the "Stage 0 findings" block of
``docs/phase10-execution-plan.md``; if EE refuses 4K, the max_dim cap holds at
1920 this phase (computePixels windowed assembly recorded as the Phase 11+ path).
"""

from __future__ import annotations

import time
import urllib.request

from openearth.catalog.registry import get_product
from openearth.ee.client import initialize
from openearth.ee.render import thumb_url
from openearth.geometry import BBox

# Richmond Park, London — the canonical acceptance ROI (no preset exists; the
# closest preset "London (UK)" is far larger, so the park bbox is explicit here).
RICHMOND_PARK = BBox(-0.30, 51.42, -0.25, 51.46)
# A cloud-light summer window so the composite is a real image, not a grey blank.
WINDOW = ("2023-06-01", "2023-08-31")


def _mint_and_fetch(dimensions: str) -> tuple[bool, float, int, str]:
    """Mint a thumb at *dimensions* (a "WxH" string) and fetch it.

    Returns ``(ok, seconds, n_bytes, note)``. ``ok`` is False on any EE refusal
    or a non-PNG payload; ``note`` carries the error text or the PNG dimensions.
    """
    from openearth.composites import build_mean_composite

    spec = get_product("s2", "RGB")
    image = build_mean_composite("RGB", RICHMOND_PARK, *WINDOW, source="s2")
    t0 = time.time()
    try:
        url = thumb_url(image, spec, RICHMOND_PARK, vis_min=0.0, vis_max=0.3, dimensions=dimensions)
        with urllib.request.urlopen(url) as response:  # EE-minted URL
            data = response.read()
    except Exception as exc:  # the spike records any refusal verbatim
        return (False, time.time() - t0, 0, f"{type(exc).__name__}: {exc}")
    elapsed = time.time() - t0
    is_png = data.startswith(b"\x89PNG\r\n\x1a\n")
    note = "png" if is_png else f"NON-PNG payload (first 16 bytes: {data[:16]!r})"
    return (is_png, elapsed, len(data), note)


def main() -> None:
    project = initialize()
    print(f"EE project: {project}")
    print(f"ROI: Richmond Park {RICHMOND_PARK}  window: {WINDOW[0]}..{WINDOW[1]}\n")

    for label, dims in (("control 1920×1080", "1920x1080"), ("4K 3840×2160", "3840x2160")):
        ok, secs, nbytes, note = _mint_and_fetch(dims)
        verdict = "OK" if ok else "REFUSED"
        mb = nbytes / 1e6
        print(f"[{verdict}] {label} ({dims}): {secs:.2f}s, {nbytes:,} bytes ({mb:.2f} MB) — {note}")


if __name__ == "__main__":
    main()
