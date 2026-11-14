import { useEffect } from "react";
import { useCatalog } from "../../api/queries";
import { useSiteScenes } from "../../api/methaneQueries";
import type { SceneInfo } from "../../api/types";
import { productBounds } from "../../lib/coverage";
import { analysisAreaToBBox, mgrsTile, referenceCheck } from "../../lib/methane";
import { PeriodPicker } from "../explore/PeriodPicker";
import { useMethaneStore } from "../../stores/methaneStore";

export function SceneStrip() {
  const site = useMethaneStore((s) => s.selectedSite);
  const dates = useMethaneStore((s) => s.dates);
  const setDates = useMethaneStore((s) => s.setDates);
  const target = useMethaneStore((s) => s.targetSceneId);
  const setTarget = useMethaneStore((s) => s.setTarget);
  const area = useMethaneStore((s) => s.analysisArea);
  const reference = useMethaneStore((s) => s.referenceSceneId);
  const setReference = useMethaneStore((s) => s.setReference);
  const method = useMethaneStore((s) => s.params.method);
  const selectDetection = useMethaneStore((s) => s.selectDetection);
  // Scene search runs on S2 L1C — the MBSP proxy pins that collection.
  const { data: catalog } = useCatalog();
  const bounds = productBounds(catalog, "s2", "MBSP");

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

  const targetScene = scenes?.find((s) => s.scene_id === target) ?? null;
  const pinnedScene =
    reference === "auto" ? null : (scenes?.find((s) => s.scene_id === reference) ?? null);
  const pinnedCheck = targetScene && pinnedScene ? referenceCheck(targetScene, pinnedScene) : null;

  // Same rule for the pinned reference: drop it once it leaves the scene list,
  // or once a new target makes it ineligible (same overpass / out of window).
  useEffect(() => {
    if (!scenes || reference === "auto") return;
    if (!pinnedScene || (pinnedCheck && !pinnedCheck.ok)) setReference("auto");
  }, [scenes, reference, pinnedScene, pinnedCheck, setReference]);

  if (!site) return <p className="muted">Select a site to load scenes.</p>;

  return (
    <div className="scene-strip">
      <PeriodPicker period={dates} onChange={setDates} label="Search period" bounds={bounds} />

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
              <th title="MGRS tile — near UTM-zone edges one acquisition appears once per overlapping tile">
                Tile
              </th>
              <th
                title={
                  method === "mbmp"
                    ? "● clear enough to be an MBMP reference — click a dot to pin that scene as the reference (click again to return to auto)"
                    : "Clear enough to be an MBMP reference"
                }
              >
                Ref
              </th>
            </tr>
          </thead>
          <tbody>
            {(scenes ?? []).map((s) => (
              <tr
                key={s.scene_id}
                className={
                  target === s.scene_id
                    ? "scene-row active"
                    : reference === s.scene_id
                      ? "scene-row ref-pinned"
                      : "scene-row"
                }
                onClick={() => {
                  setTarget(s.scene_id, s.time);
                  // Preparing a new run: drop the reviewed detection so the map's
                  // RGB and ΔXCH4 overlay never show two different acquisitions.
                  selectDetection(null);
                }}
              >
                <td>{s.time.slice(0, 10)}</td>
                <td>{s.cloud_pct.toFixed(0)}%</td>
                <td>{s.relative_orbit}</td>
                <td>{s.spacecraft.replace("Sentinel-", "S")}</td>
                <td title={s.scene_id}>{mgrsTile(s.scene_id) ?? "—"}</td>
                <td>
                  {method === "mbmp" ? (
                    <RefPin
                      scene={s}
                      target={targetScene}
                      pinned={reference === s.scene_id}
                      onToggle={() =>
                        setReference(reference === s.scene_id ? "auto" : s.scene_id, s.time)
                      }
                    />
                  ) : (
                    <span
                      className={s.ref_ok ? "dot ok" : "dot"}
                      title={s.ref_ok ? "reference-ok" : "too cloudy"}
                    />
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {scenes && scenes.length === 0 && !isFetching ? (
        <p className="muted">No scenes in this window.</p>
      ) : null}
      {pinnedScene && pinnedCheck && pinnedCheck.warnings.length > 0 ? (
        <ul className="ref-warnings">
          {pinnedCheck.warnings.map((w) => (
            <li key={w}>⚠ Pinned reference: {w}</li>
          ))}
        </ul>
      ) : null}
      <p className="muted scene-cloud-note">
        Scenes above 80&thinsp;% cloud are filtered out; ● marks scenes clear enough (≤ 30&thinsp;%)
        to serve as an MBMP reference.
        {method === "mbmp" ? " Click a dot to pin that scene as the reference." : ""}
      </p>
    </div>
  );
}

/** The Ref cell under MBMP: a pin toggle. Clicking it never selects the row as target. */
function RefPin({
  scene,
  target,
  pinned,
  onToggle,
}: {
  scene: SceneInfo;
  target: SceneInfo | null;
  pinned: boolean;
  onToggle: () => void;
}) {
  const check = target ? referenceCheck(target, scene) : null;
  const disabled = !pinned && (!check || !check.ok);
  const title = pinned
    ? "Pinned as the MBMP reference — click to return to auto"
    : !check
      ? "Pick a target scene first"
      : !check.ok
        ? `Can't be the reference: ${check.reason}`
        : check.warnings.length > 0
          ? `Pin as reference (⚠ ${check.warnings.join("; ")})`
          : "Pin as the MBMP reference";
  return (
    <button
      className={pinned ? "ref-pin pinned" : "ref-pin"}
      disabled={disabled}
      title={title}
      aria-pressed={pinned}
      aria-label={pinned ? "Unpin reference" : "Pin as reference"}
      onClick={(e) => {
        e.stopPropagation();
        onToggle();
      }}
    >
      <span className={scene.ref_ok ? "dot ok" : "dot"} />
    </button>
  );
}
