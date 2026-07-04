"""Wind arrow overlay for Folium maps using ERA5 data."""

from __future__ import annotations

import json
from typing import Any

import folium
from branca.element import MacroElement
from jinja2 import Template


class _WindArrowLayer(MacroElement):
    """Custom Leaflet layer that renders zoom-responsive wind arrows."""

    _template = Template("""
        {% macro script(this, kwargs) %}
        (function () {
            var fg = {{ this._parent.get_name() }};
            var windData = {{ this.wind_json }};
            var maxSpeed = {{ this.max_speed }};
            var markers = [];

            function getMaxLevel(zoom) {
                if (zoom <= 9) return 0;
                if (zoom <= 11) return 1;
                return 2;
            }

            function getArrowSize(zoom, speedRatio) {
                var baseSize = 10 + (zoom - 7) * 2.5;
                baseSize = Math.max(8, Math.min(35, baseSize));
                var speedFactor = 0.3 + 0.7 * speedRatio;
                return Math.round(baseSize * speedFactor);
            }

            for (var i = 0; i < windData.length; i++) {
                var pt = windData[i];
                var speedRatio = maxSpeed > 0.1
                    ? pt.speed / maxSpeed : 0.5;
                var marker = L.marker([pt.lat, pt.lon], {
                    icon: L.divIcon({
                        html: '<div>&#x2191;</div>',
                        iconSize: [10, 10],
                        iconAnchor: [5, 5],
                        className: 'wind-arrow-icon',
                    }),
                });
                marker._windLevel = pt.density_level !== undefined
                    ? pt.density_level : 0;
                marker._speedRatio = speedRatio;
                marker._angle = pt.direction_deg;
                marker._speed = pt.speed;
                marker._onMap = false;
                marker.bindTooltip(
                    'Wind: ' + pt.speed.toFixed(1) + ' m/s, '
                    + pt.direction_deg.toFixed(0) + '\\u00b0'
                );
                markers.push(marker);
            }

            function updateArrows() {
                var map = fg._map;
                if (!map) return;
                var zoom = map.getZoom();
                var maxLevel = getMaxLevel(zoom);

                for (var i = 0; i < markers.length; i++) {
                    var m = markers[i];
                    if (m._windLevel <= maxLevel) {
                        var sz = getArrowSize(zoom, m._speedRatio);
                        m.setIcon(L.divIcon({
                            html: '<div style="'
                                + 'transform:rotate(' + m._angle.toFixed(0) + 'deg);'
                                + 'font-size:' + sz + 'px;'
                                + 'color:#1a237e;'
                                + 'text-shadow:1px 1px 2px white;'
                                + '">&#x2191;</div>',
                            iconSize: [sz, sz],
                            iconAnchor: [sz / 2, sz / 2],
                            className: 'wind-arrow-icon',
                        }));
                        if (!m._onMap) {
                            fg.addLayer(m);
                            m._onMap = true;
                        }
                    } else {
                        if (m._onMap) {
                            fg.removeLayer(m);
                            m._onMap = false;
                        }
                    }
                }
            }

            fg.on('add', function () {
                updateArrows();
                if (fg._map) {
                    fg._map.on('zoomend', updateArrows);
                }
            });
            fg.on('remove', function () {
                if (fg._map) {
                    fg._map.off('zoomend', updateArrows);
                }
            });
        })();
        {% endmacro %}
    """)

    def __init__(self, wind_points: list[dict], max_speed: float):
        super().__init__()
        self.wind_json = json.dumps(wind_points)
        self.max_speed = max_speed


def add_wind_arrows(
    wind_data: list[dict[str, Any]],
) -> folium.FeatureGroup:
    """Create a FeatureGroup with zoom-responsive wind arrows.

    Arrows progressively appear as the user zooms in and
    scale in size with both zoom level and wind speed.
    """
    fg = folium.FeatureGroup(name="Wind (ERA5)")

    if not wind_data:
        return fg

    filtered = [
        d for d in wind_data
        if d["speed"] is not None
        and d["direction_deg"] is not None
    ]

    if not filtered:
        return fg

    max_speed = max(d["speed"] for d in filtered)

    layer = _WindArrowLayer(filtered, max_speed)
    layer.add_to(fg)

    return fg
