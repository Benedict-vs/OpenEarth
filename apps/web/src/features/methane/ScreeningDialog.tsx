import { useState } from "react";
import { submitScreening, useCreateSite } from "../../api/methaneQueries";
import { subscribeJob } from "../../api/sse";
import type { Hotspot, ScreeningRequest } from "../../api/types";
import { useMethaneStore } from "../../stores/methaneStore";

/** A 0.3° box around a hotspot cell (for "create site here"). */
function cellBox(lat: number, lon: number) {
  const h = 0.15;
  return { kind: "bbox" as const, west: lon - h, south: lat - h, east: lon + h, north: lat + h };
}

export function ScreeningDialog({ onClose }: { onClose: () => void }) {
  const site = useMethaneStore((s) => s.selectedSite);
  const createSite = useCreateSite();
  const [dates, setDates] = useState({ start: "2023-06-01", end: "2023-09-01" });
  const [status, setStatus] = useState<string>("");
  const [hotspots, setHotspots] = useState<Hotspot[]>([]);

  const bbox = site
    ? site.bbox
    : { kind: "bbox" as const, west: 53.5, south: 38.0, east: 54.5, north: 39.0 };

  const run = async () => {
    setHotspots([]);
    setStatus("Submitting…");
    const body: ScreeningRequest = {
      roi: bbox,
      start: dates.start,
      end: dates.end,
      background_days: 30,
      cell_deg: 0.05,
      sigma_thresh: 2,
      top_n: 50,
    };
    const { job_id } = await submitScreening(body);
    subscribeJob(job_id, {
      onProgress: (d) => setStatus(`${d.done}/${d.total} · ${d.message ?? ""}`),
      onDone: (d) => {
        setStatus("Done");
        setHotspots(((d.result as { hotspots?: Hotspot[] }).hotspots ?? []).slice(0, 30));
      },
      onError: (d) => setStatus(`Error: ${d.detail}`),
    });
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal screening-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h3>Screen region (TROPOMI)</h3>
          <button className="mini" onClick={onClose}>
            ✕
          </button>
        </div>
        <p className="muted">
          {site ? `Region: ${site.name}` : "Region: default Turkmenistan box"}
        </p>
        <div className="date-row">
          <label>
            Start
            <input
              type="date"
              value={dates.start}
              onChange={(e) => setDates((d) => ({ ...d, start: e.target.value }))}
            />
          </label>
          <label>
            End
            <input
              type="date"
              value={dates.end}
              onChange={(e) => setDates((d) => ({ ...d, end: e.target.value }))}
            />
          </label>
        </div>
        <button className="primary" onClick={run}>
          Run screening
        </button>
        {status ? <p className="muted">{status}</p> : null}

        {hotspots.length > 0 ? (
          <table className="hotspot-table">
            <thead>
              <tr>
                <th>Lat</th>
                <th>Lon</th>
                <th title="mean enhancement">Enh (ppb)</th>
                <th title="score = mean / σ">Score</th>
                <th title="weeks flagged / observed">Wk</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {hotspots.map((h, i) => (
                <tr key={i}>
                  <td>{h.lat.toFixed(3)}</td>
                  <td>{h.lon.toFixed(3)}</td>
                  <td>{h.mean_enh_ppb.toFixed(1)}</td>
                  <td>{h.score.toFixed(1)}</td>
                  <td>
                    {h.weeks_flagged}/{h.weeks_observed}
                  </td>
                  <td>
                    <button
                      className="mini"
                      onClick={() =>
                        createSite.mutate({
                          name: `Hotspot ${h.lat.toFixed(2)},${h.lon.toFixed(2)}`,
                          bbox: cellBox(h.lat, h.lon),
                        })
                      }
                    >
                      + Site
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
      </div>
    </div>
  );
}
