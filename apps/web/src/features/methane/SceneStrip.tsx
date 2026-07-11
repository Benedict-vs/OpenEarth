import { useEffect } from "react";
import { useSiteScenes } from "../../api/methaneQueries";
import { analysisAreaToBBox } from "../../lib/methane";
import { useMethaneStore } from "../../stores/methaneStore";

export function SceneStrip() {
  const site = useMethaneStore((s) => s.selectedSite);
  const dates = useMethaneStore((s) => s.dates);
  const setDates = useMethaneStore((s) => s.setDates);
  const target = useMethaneStore((s) => s.targetSceneId);
  const setTarget = useMethaneStore((s) => s.setTarget);
  const area = useMethaneStore((s) => s.analysisArea);

  // Search over the analysis area so every listed scene covers the chip.
  const {
    data: scenes,
    isFetching,
    error,
  } = useSiteScenes(site?.id ?? null, dates.start, dates.end, 80, area && analysisAreaToBBox(area));

  // A moved analysis area can invalidate the picked scene (different S2 tile):
  // drop the target if it is no longer in the (area-scoped) scene list.
  useEffect(() => {
    if (scenes && target && !scenes.some((s) => s.scene_id === target)) setTarget(null);
  }, [scenes, target, setTarget]);

  if (!site) return <p className="muted">Select a site to load scenes.</p>;

  return (
    <div className="scene-strip">
      <div className="date-row">
        <label>
          Start
          <input
            type="date"
            value={dates.start}
            onChange={(e) => setDates(e.target.value, dates.end)}
          />
        </label>
        <label>
          End
          <input
            type="date"
            value={dates.end}
            onChange={(e) => setDates(dates.start, e.target.value)}
          />
        </label>
      </div>

      {error ? (
        <p className="error-text">{error instanceof Error ? error.message : "Scene load failed"}</p>
      ) : null}
      {isFetching ? <p className="muted">Loading scenes…</p> : null}

      <div className="scene-table-wrap">
        <table className="scene-table">
          <thead>
            <tr>
              <th>Date</th>
              <th>Cloud</th>
              <th>Orbit</th>
              <th>Sat</th>
              <th title="Clear enough to be an MBMP reference">Ref</th>
            </tr>
          </thead>
          <tbody>
            {(scenes ?? []).map((s) => (
              <tr
                key={s.scene_id}
                className={target === s.scene_id ? "scene-row active" : "scene-row"}
                onClick={() => setTarget(s.scene_id, s.time)}
              >
                <td>{s.time.slice(0, 10)}</td>
                <td>{s.cloud_pct.toFixed(0)}%</td>
                <td>{s.relative_orbit}</td>
                <td>{s.spacecraft.replace("Sentinel-", "S")}</td>
                <td>
                  <span
                    className={s.ref_ok ? "dot ok" : "dot"}
                    title={s.ref_ok ? "reference-ok" : "too cloudy"}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {scenes && scenes.length === 0 && !isFetching ? (
        <p className="muted">No scenes in this window.</p>
      ) : null}
      <p className="muted scene-cloud-note">
        Scenes above 80&thinsp;% cloud are filtered out; ● marks scenes clear enough (≤ 30&thinsp;%)
        to serve as an MBMP reference.
      </p>
    </div>
  );
}
