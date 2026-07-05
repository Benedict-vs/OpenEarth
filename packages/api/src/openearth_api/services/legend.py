"""Legend derivation — pure, no Earth Engine."""

from __future__ import annotations

from typing import TYPE_CHECKING

from openearth_api.schemas import LegendOut

if TYPE_CHECKING:
    from openearth.catalog.models import ProductSpec
    from openearth_api.schemas import VizOverrides


def legend_for(spec: ProductSpec, viz: VizOverrides | None) -> LegendOut:
    vis_min = viz.vis_min if viz and viz.vis_min is not None else spec.vis_min
    vis_max = viz.vis_max if viz and viz.vis_max is not None else spec.vis_max
    return LegendOut(
        min=vis_min,
        max=vis_max,
        unit=spec.display_unit,
        palette=list(spec.palette),
        display_scale=spec.display_scale,
        is_rgb=spec.is_rgb,
        description=spec.description,
    )
