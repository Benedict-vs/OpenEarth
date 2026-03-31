"""Tab 1: Spatial Map -- single heatmap with date/mean toggle."""

from __future__ import annotations

import math
from datetime import date
from typing import cast

import ee
import folium
import streamlit as st
from streamlit_folium import st_folium

from openearth.providers import get_config
from openearth.providers.gee_session import initialize_ee
from openearth.visualization.heatmap import (
    create_heatmap_folium,
)

from app.analysis import (
    cached_date_tile_url,
    cached_mean_tile_url,
    cached_vis_range,
    render_color_legend,
)
from app.errors import show_ee_error
from app.roi import map_center

_SAT_LABEL = {
    "s5p": "Sentinel-5P",
    "s2": "Sentinel-2",
}


def _ee_fetch_timeout(
    dimensions: int,
    west: float,
    south: float,
    east: float,
    north: float,
) -> int:
    """Dynamic timeout for Earth Engine thumbnail fetch.

    Scales with image dimensions and ROI area (degree²,
    cosine-corrected for latitude).
    """
    mid_lat = math.radians((south + north) / 2)
    area_deg2 = (
        abs(east - west)
        * math.cos(mid_lat)
        * abs(north - south)
    )
    dim_factor = dimensions / 512
    area_factor = max(1.0, area_deg2 / 25)
    timeout = int(120 * dim_factor * area_factor)
    return min(timeout, 600)


def _scale_controls(
    data_key: str,
    source: str,
    hp: dict,
) -> tuple[float | None, float | None]:
    """Render scale-adjustment controls.

    Returns ``(vis_min, vis_max)`` — both *None*
    when the user keeps the default scale.
    """
    cfg = get_config(data_key, source)
    scale = cfg.display_scale
    unit = cfg.display_unit

    min_key = "vis_min"
    max_key = "vis_max"

    # Reset sliders when the variable or source changes.
    _scale_id = f"{data_key}|{source}"
    if st.session_state.get("_scale_var_id") != _scale_id:
        st.session_state["_scale_var_id"] = _scale_id
        st.session_state[min_key] = (
            cfg.vis_min * scale
        )
        st.session_state[max_key] = (
            cfg.vis_max * scale
        )
        st.session_state.pop("auto_scale", None)
        st.session_state.pop("_prev_auto_scale", None)

    # Initialise slider session state on first run.
    if min_key not in st.session_state:
        st.session_state[min_key] = (
            cfg.vis_min * scale
        )
    if max_key not in st.session_state:
        st.session_state[max_key] = (
            cfg.vis_max * scale
        )

    with st.expander("Scale settings"):
        auto = st.checkbox(
            "Auto-compute from data",
            key="auto_scale",
        )

        # Detect toggle change
        prev_auto = st.session_state.get(
            "_prev_auto_scale",
        )
        toggled = auto != prev_auto
        st.session_state["_prev_auto_scale"] = auto

        if auto and toggled:
            try:
                with st.spinner(
                    "Computing data range..."
                ):
                    auto_min, auto_max = (
                        cached_vis_range(
                            data_key,
                            hp["west"],
                            hp["south"],
                            hp["east"],
                            hp["north"],
                            hp["start_date"],
                            hp["end_date"],
                            source=source,
                        )
                    )
                st.session_state[min_key] = (
                    auto_min * scale
                )
                st.session_state[max_key] = (
                    auto_max * scale
                )
            except ee.EEException:
                st.warning(
                    "Auto-scale computation failed "
                    "(ROI may be too large). "
                    "Using default range."
                )
                st.session_state["auto_scale"] = False
                st.session_state[
                    "_prev_auto_scale"
                ] = False

        if not auto and toggled:
            st.session_state[min_key] = (
                cfg.vis_min * scale
            )
            st.session_state[max_key] = (
                cfg.vis_max * scale
            )

        st.slider(
            f"Min ({unit})",
            min_value=cfg.valid_min * scale,
            max_value=cfg.valid_max * scale,
            key=min_key,
        )
        st.slider(
            f"Max ({unit})",
            min_value=cfg.valid_min * scale,
            max_value=cfg.valid_max * scale,
            key=max_key,
        )

    raw_min = st.session_state[min_key] / scale
    raw_max = st.session_state[max_key] / scale

    uses_default = (
        abs(raw_min - cfg.vis_min) < 1e-12
        and abs(raw_max - cfg.vis_max) < 1e-12
    )
    if uses_default:
        return (None, None)
    return (raw_min, raw_max)


def render(
    authenticate_on_fail: bool,
) -> None:
    if "heatmap_params" not in st.session_state:
        st.info("Run an analysis first.")
        return

    hp = st.session_state["heatmap_params"]
    data_key = hp["data_key"]
    source = hp.get("source", "s5p")
    sat = _SAT_LABEL.get(source, "Sentinel-5P")

    try:
        initialize_ee(
            project_id=hp["project_id"],
            authenticate=authenticate_on_fail,
        )
    except ee.EEException as exc:
        show_ee_error(
            exc,
            "Could not initialize Earth Engine "
            "for map rendering.",
        )
        st.stop()

    center_lat, center_lon = map_center(
        hp["west"], hp["south"],
        hp["east"], hp["north"],
    )
    bounds = [
        [hp["south"], hp["west"]],
        [hp["north"], hp["east"]],
    ]

    # ── Mode toggle ──────────────────────────────────
    mode = st.radio(
        "Composite type",
        options=["Date composite", "Mean composite"],
        horizontal=True,
        key="heatmap_mode",
    )

    # ── Date controls (only for date mode) ───────────
    selected_date = None
    half_window = 0
    window_label = ""

    from datetime import timedelta as _td

    _start = date.fromisoformat(hp["start_date"])
    _end = date.fromisoformat(hp["end_date"])
    available_dates = [
        _start + _td(days=i)
        for i in range((_end - _start).days)
    ]

    if mode == "Date composite":
        if len(available_dates) < 2:
            st.info(
                "Need at least 2 dates to "
                "use the date composite."
            )
            return

        selected_date = cast(
            date,
            st.select_slider(
                "Select date",
                options=available_dates,
                value=available_dates[
                    len(available_dates) // 2
                ],
                key="heatmap_date_slider",
            ),
        )
        half_window = st.slider(
            "Composite window (+/- days)",
            min_value=0,
            max_value=14,
            value=7,
            help=(
                "Days before and after the "
                "selected date to include."
            ),
            key="heatmap_half_window",
        )
        window_label = (
            f"{selected_date}"
            if half_window == 0
            else (
                f"{selected_date} "
                f"+/- {half_window} days"
            )
        )
        st.caption(f"Showing: {window_label}")
    else:
        st.caption(
            f"Composite mean of all {sat} passes "
            f"from {hp['start_date']} to "
            f"{hp['end_date']}"
        )

    # ── Scale controls ───────────────────────────────
    vis_min, vis_max = _scale_controls(
        data_key, source, hp,
    )

    # ── Render heatmap ───────────────────────────────
    try:
        if mode == "Date composite":
            with st.spinner(
                f"Loading {data_key} heatmap for "
                f"{window_label}..."
            ):
                tile_url = cached_date_tile_url(
                    data_key,
                    hp["west"],
                    hp["south"],
                    hp["east"],
                    hp["north"],
                    selected_date.isoformat(),
                    half_window,
                    source=source,
                    vis_min=vis_min,
                    vis_max=vis_max,
                )
            layer_name = (
                f"{data_key} {window_label}"
            )
        else:
            with st.spinner(
                f"Loading mean {data_key} heatmap..."
            ):
                tile_url = cached_mean_tile_url(
                    data_key,
                    hp["west"],
                    hp["south"],
                    hp["east"],
                    hp["north"],
                    hp["start_date"],
                    hp["end_date"],
                    source=source,
                    vis_min=vis_min,
                    vis_max=vis_max,
                )
            layer_name = f"Mean {data_key}"

        base_map, fg = create_heatmap_folium(
            tile_url=tile_url,
            center_lat=center_lat,
            center_lon=center_lon,
            bounds=bounds,
            layer_name=layer_name,
            source=source,
        )
        st_folium(
            base_map,
            key="heatmap",
            feature_group_to_add=fg,
            layer_control=folium.LayerControl(),
            height=500,
            width=None,
            returned_objects=[],
        )

    except ee.EEException as exc:
        show_ee_error(
            exc,
            "Could not render heatmap.",
        )

    render_color_legend(
        data_key, source,
        vis_min=vis_min,
        vis_max=vis_max,
    )

    # ── Store current heatmap state for export ───────
    st.session_state["current_heatmap"] = {
        "mode": mode,
        "selected_date": (
            selected_date.isoformat()
            if selected_date
            else None
        ),
        "half_window": half_window,
        "vis_min": vis_min,
        "vis_max": vis_max,
    }

    # ── Image export ─────────────────────────────────
    _render_image_export(
        hp, data_key, source, available_dates,
    )


def _export_fingerprint(
    hp: dict,
    data_key: str,
    source: str,
    hm: dict,
) -> str:
    """Build a fingerprint to detect stale exports."""
    return (
        f"{data_key}|{source}"
        f"|{hm.get('mode')}"
        f"|{hm.get('selected_date')}"
        f"|{hm.get('half_window')}"
        f"|{hm.get('vis_min')}"
        f"|{hm.get('vis_max')}"
        f"|{hp.get('start_date')}"
        f"|{hp.get('end_date')}"
    )


def _render_image_export(
    hp: dict,
    data_key: str,
    source: str,
    available_dates: list[date] | None = None,
) -> None:
    """Render image export controls below the heatmap."""
    import io
    import itertools
    import traceback
    import urllib.request
    import zipfile

    from app.analysis import (
        cached_thumb_url,
        cached_date_thumb_url,
        cached_download_url,
    )
    from app.config import TRACE_GASES, S2_INDICES
    from app.errors import show_ee_error, show_image_error

    hm = st.session_state.get("current_heatmap", {})
    mode = hm.get("mode", "Mean composite")

    # Clear stale exports when heatmap params change.
    fp = _export_fingerprint(hp, data_key, source, hm)
    if st.session_state.get("_export_fp") != fp:
        st.session_state["_export_fp"] = fp
        st.session_state.pop("export_img_bytes", None)
        st.session_state.pop("export_img_meta", None)
        st.session_state.pop("export_tiff_url", None)
        st.session_state.pop("batch_results", None)

    with st.expander("Export Image"):
        img_type = st.selectbox(
            "Format",
            options=["PNG", "JPEG", "GeoTIFF"],
            key="export_format",
        )

        if img_type in ("PNG", "JPEG"):
            dimensions = st.slider(
                "Image size (longest edge, px)",
                min_value=256,
                max_value=4096,
                value=1024,
                step=256,
                key="export_dimensions",
                help=(
                    "Controls the longest edge of "
                    "the exported image in pixels."
                ),
            )

            fmt_map = {"PNG": "png", "JPEG": "jpg"}
            fmt = fmt_map[img_type]
            mime = (
                "image/png" if fmt == "png"
                else "image/jpeg"
            )
            ext = "png" if fmt == "png" else "jpeg"

            if st.button(
                f"Generate {img_type}",
                key=f"gen_{fmt}",
            ):
                try:
                    with st.spinner(
                        "Generating image..."
                    ):
                        if mode == "Date composite":
                            thumb_url = (
                                cached_date_thumb_url(
                                    data_key,
                                    hp["west"],
                                    hp["south"],
                                    hp["east"],
                                    hp["north"],
                                    hm["selected_date"],
                                    hm["half_window"],
                                    source=source,
                                    vis_min=hm.get(
                                        "vis_min",
                                    ),
                                    vis_max=hm.get(
                                        "vis_max",
                                    ),
                                    dimensions=dimensions,
                                    img_format=fmt,
                                )
                            )
                        else:
                            thumb_url = (
                                cached_thumb_url(
                                    data_key,
                                    hp["west"],
                                    hp["south"],
                                    hp["east"],
                                    hp["north"],
                                    hp["start_date"],
                                    hp["end_date"],
                                    source=source,
                                    vis_min=hm.get(
                                        "vis_min",
                                    ),
                                    vis_max=hm.get(
                                        "vis_max",
                                    ),
                                    dimensions=dimensions,
                                    img_format=fmt,
                                )
                            )
                        _timeout = _ee_fetch_timeout(
                            dimensions,
                            hp["west"],
                            hp["south"],
                            hp["east"],
                            hp["north"],
                        )
                        with urllib.request.urlopen(
                            thumb_url, timeout=_timeout,
                        ) as resp:
                            img_bytes = resp.read()

                    fname = (
                        f"openearth_{data_key}"
                        f"_{hp['start_date']}"
                        f"_{hp['end_date']}"
                        f".{ext}"
                    )
                    st.session_state[
                        "export_img_bytes"
                    ] = img_bytes
                    st.session_state[
                        "export_img_meta"
                    ] = {
                        "format": img_type,
                        "mime": mime,
                        "fname": fname,
                        "caption": (
                            f"{data_key} composite"
                        ),
                    }
                except Exception as exc:
                    show_image_error(
                        exc,
                        "Could not generate image.",
                    )

            # Show persisted image outside button block.
            stored = st.session_state.get(
                "export_img_bytes",
            )
            meta = st.session_state.get(
                "export_img_meta",
            )
            if stored and meta:
                st.image(
                    stored,
                    caption=meta["caption"],
                )
                st.download_button(
                    label=(
                        f"Download {meta['format']}"
                    ),
                    data=stored,
                    file_name=meta["fname"],
                    mime=meta["mime"],
                    key="dl_export",
                )

        elif img_type == "GeoTIFF":
            st.info(
                "GeoTIFF exports the raw raster data "
                "(single band) for use in GIS software "
                "such as QGIS or ArcGIS."
            )
            if st.button(
                "Generate GeoTIFF link",
                key="gen_tiff",
            ):
                try:
                    with st.spinner(
                        "Preparing GeoTIFF..."
                    ):
                        dl_url = cached_download_url(
                            data_key,
                            hp["west"],
                            hp["south"],
                            hp["east"],
                            hp["north"],
                            hp["start_date"],
                            hp["end_date"],
                            source=source,
                        )
                    st.session_state[
                        "export_tiff_url"
                    ] = dl_url
                except Exception as exc:
                    show_image_error(
                        exc,
                        "Could not generate GeoTIFF.",
                    )

            # Show persisted GeoTIFF link.
            stored_tiff = st.session_state.get(
                "export_tiff_url",
            )
            if stored_tiff:
                st.markdown(
                    f"[Download GeoTIFF]"
                    f"({stored_tiff})"
                )

        # ── Batch Export ──────────────────────────────
        st.divider()
        st.subheader("Batch Export")

        variables = (
            S2_INDICES if source == "s2"
            else TRACE_GASES
        )

        batch_mode = st.radio(
            "Mode",
            ["Single dates", "Period averages"],
            horizontal=True,
            key="batch_mode",
        )

        if batch_mode == "Single dates":
            batch_dates = st.multiselect(
                "Dates",
                options=available_dates or [],
                default=[],
                key="batch_dates",
                help="Select dates to export.",
            )

        else:
            _PERIOD_OPTIONS = {
                "1 month": 1,
                "2 months": 2,
                "3 months": 3,
                "6 months": 6,
                "1 year": 12,
            }
            period_label = st.selectbox(
                "Period length",
                options=list(_PERIOD_OPTIONS.keys()),
                key="batch_period_length",
            )
            period_months = _PERIOD_OPTIONS[period_label]

            from datetime import date as _date

            sidebar_start = _date.fromisoformat(
                hp["start_date"],
            )
            sidebar_end = _date.fromisoformat(
                hp["end_date"],
            )
            pcol1, pcol2 = st.columns(2)
            with pcol1:
                range_start = st.date_input(
                    "Range start",
                    value=sidebar_start,
                    key="batch_range_start",
                )
            with pcol2:
                range_end = st.date_input(
                    "Range end",
                    value=sidebar_end,
                    key="batch_range_end",
                )

            def _period_windows(start, end, months):
                """Generate (start, end) for each period."""
                windows = []
                cur = start
                while cur < end:
                    y = cur.year + (
                        (cur.month - 1 + months) // 12
                    )
                    m = (cur.month - 1 + months) % 12 + 1
                    nxt = cur.replace(
                        year=y, month=m, day=1,
                    )
                    if nxt > end:
                        nxt = end
                    windows.append((cur, nxt))
                    cur = nxt
                return windows

            period_windows = _period_windows(
                range_start, range_end, period_months,
            )

        batch_vars = st.multiselect(
            "Variables",
            options=list(variables.keys()),
            format_func=lambda k: variables[k],
            default=[data_key],
            key="batch_vars",
            help="Select variables to export.",
        )

        batch_fmt = st.selectbox(
            "Batch format",
            options=["PNG", "JPEG"],
            key="batch_format",
        )
        batch_dims = st.slider(
            "Batch image size (longest edge, px)",
            min_value=256,
            max_value=4096,
            value=1024,
            step=256,
            key="batch_dimensions",
        )

        b_fmt_map = {"PNG": "png", "JPEG": "jpg"}
        b_fmt = b_fmt_map[batch_fmt]
        b_mime = (
            "image/png" if b_fmt == "png"
            else "image/jpeg"
        )
        b_ext = "png" if b_fmt == "png" else "jpeg"

        _MAX_BATCH = 20

        if batch_mode == "Single dates":
            combos = list(
                itertools.product(
                    batch_dates, batch_vars,
                ),
            )
            if len(combos) > _MAX_BATCH:
                st.warning(
                    f"Batch limited to {_MAX_BATCH} "
                    f"combinations "
                    f"(selected {len(combos)}). "
                    "Reduce dates or variables."
                )
                combos = combos[:_MAX_BATCH]
        else:
            combos = list(
                itertools.product(
                    period_windows, batch_vars,
                ),
            )
            if len(combos) > _MAX_BATCH:
                st.warning(
                    f"Batch limited to {_MAX_BATCH} "
                    f"combinations "
                    f"(selected {len(combos)}). "
                    "Reduce period range or variables."
                )
                combos = combos[:_MAX_BATCH]

        can_generate = len(combos) > 0
        if can_generate:
            if batch_mode == "Period averages":
                n_periods = len(period_windows)
                st.caption(
                    f"{n_periods} period(s) x "
                    f"{len(batch_vars)} variable(s) = "
                    f"{len(combos)} image(s) "
                    "will be generated."
                )
            else:
                st.caption(
                    f"{len(combos)} image(s) will be "
                    "generated."
                )

        if st.button(
            "Generate Batch",
            key="gen_batch",
            disabled=not can_generate,
        ):
            results: list[dict] = []
            progress = st.progress(
                0,
                text=(
                    f"Generating batch "
                    f"(0/{len(combos)})..."
                ),
            )
            for i, combo in enumerate(combos):
                if batch_mode == "Single dates":
                    d, var = combo
                    label = str(d)
                    progress.progress(
                        i / len(combos),
                        text=(
                            f"Generating {var} @ "
                            f"{label} "
                            f"({i + 1}/{len(combos)})..."
                        ),
                    )
                    try:
                        thumb_url = (
                            cached_date_thumb_url(
                                var,
                                hp["west"],
                                hp["south"],
                                hp["east"],
                                hp["north"],
                                d.isoformat(),
                                hm.get(
                                    "half_window", 7,
                                ),
                                source=source,
                                vis_min=(
                                    hm.get("vis_min")
                                    if var == data_key
                                    else None
                                ),
                                vis_max=(
                                    hm.get("vis_max")
                                    if var == data_key
                                    else None
                                ),
                                dimensions=batch_dims,
                                img_format=b_fmt,
                            )
                        )
                        fname = (
                            f"openearth_{var}"
                            f"_{d}.{b_ext}"
                        )
                    except Exception as exc:
                        results.append({
                            "date": label,
                            "var": var,
                            "bytes": None,
                            "fname": None,
                            "error": str(exc),
                            "traceback": (
                                traceback.format_exc()
                            ),
                        })
                        progress.progress(
                            (i + 1) / len(combos),
                            text=(
                                f"Failed {var} @ "
                                f"{label} "
                                f"({i + 1}/{len(combos)})"
                            ),
                        )
                        continue
                else:
                    (p_start, p_end), var = combo
                    label = (
                        f"{p_start.isoformat()} to "
                        f"{p_end.isoformat()}"
                    )
                    progress.progress(
                        i / len(combos),
                        text=(
                            f"Generating {var} "
                            f"mean {label} "
                            f"({i + 1}/{len(combos)})..."
                        ),
                    )
                    try:
                        thumb_url = cached_thumb_url(
                            var,
                            hp["west"],
                            hp["south"],
                            hp["east"],
                            hp["north"],
                            p_start.isoformat(),
                            p_end.isoformat(),
                            source=source,
                            vis_min=(
                                hm.get("vis_min")
                                if var == data_key
                                else None
                            ),
                            vis_max=(
                                hm.get("vis_max")
                                if var == data_key
                                else None
                            ),
                            dimensions=batch_dims,
                            img_format=b_fmt,
                        )
                        fname = (
                            f"openearth_{var}"
                            f"_{p_start}_{p_end}"
                            f".{b_ext}"
                        )
                    except Exception as exc:
                        results.append({
                            "date": label,
                            "var": var,
                            "bytes": None,
                            "fname": None,
                            "error": str(exc),
                            "traceback": (
                                traceback.format_exc()
                            ),
                        })
                        progress.progress(
                            (i + 1) / len(combos),
                            text=(
                                f"Failed {var} @ "
                                f"{label} "
                                f"({i + 1}/{len(combos)})"
                            ),
                        )
                        continue

                try:
                    _timeout = _ee_fetch_timeout(
                        batch_dims,
                        hp["west"],
                        hp["south"],
                        hp["east"],
                        hp["north"],
                    )
                    with urllib.request.urlopen(
                        thumb_url, timeout=_timeout,
                    ) as resp:
                        img_bytes = resp.read()
                    results.append({
                        "date": label,
                        "var": var,
                        "bytes": img_bytes,
                        "fname": fname,
                        "error": None,
                    })
                except Exception as exc:
                    results.append({
                        "date": label,
                        "var": var,
                        "bytes": None,
                        "fname": None,
                        "error": str(exc),
                        "traceback": (
                            traceback.format_exc()
                        ),
                    })
                progress.progress(
                    (i + 1) / len(combos),
                    text=(
                        f"Done {var} @ {label} "
                        f"({i + 1}/{len(combos)})"
                    ),
                )
            progress.empty()
            st.session_state["batch_results"] = results

        # Show persisted batch results.
        batch = st.session_state.get("batch_results")
        if batch:
            ok = [r for r in batch if r["bytes"]]
            fail = [r for r in batch if r["error"]]

            if fail:
                st.warning(
                    f"{len(fail)} image(s) failed.",
                )
                for r in fail:
                    st.caption(
                        f"{r['var']} @ {r['date']}: "
                        f"{r['error']}"
                    )
                    tb = r.get("traceback")
                    if tb:
                        with st.expander(
                            "Error details",
                            expanded=False,
                        ):
                            st.code(
                                tb, language="text",
                            )

            for r in ok:
                st.image(
                    r["bytes"],
                    caption=(
                        f"{r['var']} — {r['date']}"
                    ),
                )
                st.download_button(
                    label=f"Download {r['fname']}",
                    data=r["bytes"],
                    file_name=r["fname"],
                    mime=b_mime,
                    key=(
                        f"dl_batch"
                        f"_{r['var']}_{r['date']}"
                    ),
                )

            if len(ok) > 1:
                zip_buf = io.BytesIO()
                with zipfile.ZipFile(
                    zip_buf, "w",
                    zipfile.ZIP_DEFLATED,
                ) as zf:
                    for r in ok:
                        zf.writestr(
                            r["fname"], r["bytes"],
                        )
                zip_buf.seek(0)
                st.download_button(
                    "Download All (ZIP)",
                    data=zip_buf.getvalue(),
                    file_name=(
                        f"openearth_batch"
                        f"_{hp['start_date']}"
                        f"_{hp['end_date']}"
                        f".zip"
                    ),
                    mime="application/zip",
                    key="dl_batch_zip",
                )
