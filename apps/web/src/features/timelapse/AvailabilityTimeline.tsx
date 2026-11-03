import type { Preflight } from "../../api/types";

interface Props {
  preflight: Preflight | null;
  loading: boolean;
  error: string | null;
  /** The render's primary dataset id, to label fallbacks honestly. */
  primary: string;
}

/**
 * The filmstrip-timeline availability strip (decision 11) — one cell per window,
 * showing scene density and which source the frame will come from, *before* any
 * render. Empty spans read as "no data here", not a failed render later.
 * Everything here is the cheap preflight probe: counts + source, never pixels.
 */
export function AvailabilityTimeline({ preflight, loading, error, primary }: Props) {
  if (error) {
    return <p className="cut-strip-msg error-text">Availability probe failed: {error}</p>;
  }
  if (!preflight) {
    return (
      <p className="cut-strip-msg muted" role="status" aria-live="polite">
        {loading
          ? "Checking availability…"
          : "Pick a region, product, and period to see availability."}
      </p>
    );
  }

  const windows = preflight.windows;
  const maxCount = Math.max(1, ...windows.map((w) => w.scene_count));
  const fallbacks = windows.filter((w) => w.scene_count > 0 && w.source !== primary).length;
  const warned = windows.filter((w) => w.advisory != null).length;

  return (
    <div className={`cut-avail ${loading ? "stale" : ""}`}>
      <div className="cut-strip-head">
        <span className="cut-strip-title">
          Availability <b>{windows.length} windows</b>
        </span>
        <span className="cut-strip-summary mono">
          {preflight.frame_count} frames · {preflight.empty_count} gap
          {preflight.empty_count === 1 ? "" : "s"}
          {fallbacks > 0 ? ` · ${fallbacks} via fallback` : ""}
        </span>
      </div>

      <div className="cut-film" role="list" aria-label="Per-window availability">
        {windows.map((w, i) => {
          const empty = w.scene_count === 0;
          const kind = empty ? "gap" : w.source === primary ? "primary" : "fallback";
          const warn = w.advisory != null;
          const height = empty ? 0 : Math.max(14, Math.round((w.scene_count / maxCount) * 100));
          const detail = empty
            ? `${w.label}: no scenes`
            : `${w.label}: ${w.scene_count} scene${w.scene_count === 1 ? "" : "s"} · ${w.source.toUpperCase()}`;
          return (
            <div
              key={`${w.start}-${i}`}
              className={`cut-cell ${kind}`}
              role="listitem"
              title={warn ? `${detail} · ⚠ ${w.advisory}` : detail}
            >
              <div className="cut-cell-bar-track">
                {empty ? (
                  <div className="cut-cell-nodata">—</div>
                ) : (
                  <div
                    className={`cut-cell-bar ${kind}${warn ? " warn" : ""}`}
                    style={{ height: `${height}%` }}
                  />
                )}
              </div>
              <div className={`cut-cell-src ${kind}`}>{empty ? "—" : w.source.toUpperCase()}</div>
              <div className="cut-cell-label mono">{w.label}</div>
            </div>
          );
        })}
      </div>

      <div className="cut-avail-legend">
        <span>
          <i className="sw primary" /> {primary.toUpperCase()}
        </span>
        {fallbacks > 0 ? (
          <span>
            <i className="sw fallback" /> Fallback
          </span>
        ) : null}
        {preflight.empty_count > 0 ? (
          <span>
            <i className="sw gap" /> No data
          </span>
        ) : null}
        {warned > 0 ? (
          <span title="Only striped Landsat-7 SLC-off scenes in these windows — expect diagonal wedge gaps. Widen the window to composite them away.">
            <i className="sw warn" /> Wedge gaps likely
          </span>
        ) : null}
        <span className="cut-avail-note">Bar height = scenes in the window</span>
      </div>
    </div>
  );
}
