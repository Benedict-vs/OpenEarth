"""Wind arrow overlay for Folium maps using ERA5 data."""

from __future__ import annotations

from typing import Any

import folium


def add_wind_arrows(
    wind_data: list[dict[str, Any]],
    max_arrow_size: float = 30.0,
) -> folium.FeatureGroup:
    """Create a FeatureGroup with rotated arrow markers.

    Each arrow is a ``DivIcon`` with a rotated CSS arrow.
    Arrow size scales with wind speed.
    """
    fg = folium.FeatureGroup(name="Wind (ERA5)")

    if not wind_data:
        return fg

    speeds = [
        d["speed"]
        for d in wind_data
        if d["speed"] is not None
    ]
    max_speed = max(speeds) if speeds else 1.0

    for point in wind_data:
        if (
            point["speed"] is None
            or point["direction_deg"] is None
        ):
            continue

        # Scale arrow size by wind speed.
        size = max(
            8,
            (point["speed"] / max(max_speed, 0.1))
            * max_arrow_size,
        )
        angle = point["direction_deg"]

        html = (
            f'<div style="'
            f"transform: rotate({angle:.0f}deg);"
            f"font-size: {size:.0f}px;"
            f"color: #1a237e;"
            f"text-shadow: 1px 1px 2px white;"
            f'">&#x2191;</div>'
        )

        icon = folium.DivIcon(
            html=html,
            icon_size=(int(size), int(size)),
            icon_anchor=(int(size / 2), int(size / 2)),
        )

        tooltip = (
            f"Wind: {point['speed']:.1f} m/s, "
            f"{point['direction_deg']:.0f}\u00b0"
        )

        folium.Marker(
            location=[point["lat"], point["lon"]],
            icon=icon,
            tooltip=tooltip,
        ).add_to(fg)

    return fg
