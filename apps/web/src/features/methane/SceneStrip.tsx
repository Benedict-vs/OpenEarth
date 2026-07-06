import { useSiteScenes } from "../../api/methaneQueries";
import { useMethaneStore } from "../../stores/methaneStore";

export function SceneStrip() {
  const site = useMethaneStore((s) => s.selectedSite);
  const dates = useMethaneStore((s) => s.dates);
  const setDates = useMethaneStore((s) => s.setDates);
  const target = useMethaneStore((s) => s.targetSceneId);
  const setTarget = useMethaneStore((s) => s.setTarget);

  const {
    data: scenes,
    isFetching,
    error,
  } = useSiteScenes(site?.id ?? null, dates.start, dates.end);

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
                onClick={() => setTarget(s.scene_id)}
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
    </div>
  );
}
