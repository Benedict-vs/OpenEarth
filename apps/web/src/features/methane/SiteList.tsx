import { useState } from "react";
import { useCreateSite, useDeleteSite, useSites } from "../../api/methaneQueries";
import type { SiteIn } from "../../api/types";
import { useMethaneStore } from "../../stores/methaneStore";

export function SiteList({ onScreen }: { onScreen: () => void }) {
  const { data: sites, isLoading } = useSites();
  const selected = useMethaneStore((s) => s.selectedSite);
  const selectSite = useMethaneStore((s) => s.selectSite);
  const createSite = useCreateSite();
  const deleteSite = useDeleteSite();
  const [creating, setCreating] = useState(false);

  return (
    <div className="site-list">
      <div className="panel-head">
        <h3>Sites</h3>
        <div className="panel-head-actions">
          <button className="mini" onClick={() => setCreating((v) => !v)} title="Add a watch site">
            {creating ? "Cancel" : "+ New"}
          </button>
          <button
            className="mini"
            onClick={onScreen}
            title="Coarse TROPOMI (S5P) pre-screen of a whole region for persistent XCH4 hotspots — a where-to-look pass before Sentinel-2 analysis"
          >
            Screen
          </button>
        </div>
      </div>

      {creating ? (
        <NewSiteForm
          busy={createSite.isPending}
          error={createSite.error instanceof Error ? createSite.error.message : null}
          onSubmit={(body) => createSite.mutate(body, { onSuccess: () => setCreating(false) })}
        />
      ) : null}

      {isLoading ? <p className="muted">Loading…</p> : null}
      <ul className="site-items">
        {(sites ?? []).map((site) => (
          <li
            key={site.id}
            className={selected?.id === site.id ? "site-item active" : "site-item"}
            onClick={() => selectSite(site)}
          >
            <span className="site-name">{site.name}</span>
            <button
              className="mini danger"
              title="Delete site"
              onClick={(e) => {
                e.stopPropagation();
                if (confirm(`Delete site "${site.name}"?`)) deleteSite.mutate(site.id);
              }}
            >
              ✕
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function NewSiteForm({
  busy,
  error,
  onSubmit,
}: {
  busy: boolean;
  error: string | null;
  onSubmit: (body: SiteIn) => void;
}) {
  const [name, setName] = useState("");
  const [box, setBox] = useState({ west: "", south: "", east: "", north: "" });

  const submit = () => {
    const west = Number(box.west);
    const south = Number(box.south);
    const east = Number(box.east);
    const north = Number(box.north);
    if (!name || [west, south, east, north].some((v) => Number.isNaN(v))) return;
    onSubmit({ name, bbox: { kind: "bbox", west, south, east, north } });
  };

  return (
    <div className="new-site-form">
      <input placeholder="Site name" value={name} onChange={(e) => setName(e.target.value)} />
      <div className="bbox-grid">
        {(["west", "south", "east", "north"] as const).map((k) => (
          <input
            key={k}
            placeholder={k}
            value={box[k]}
            onChange={(e) => setBox((b) => ({ ...b, [k]: e.target.value }))}
          />
        ))}
      </div>
      {error ? <p className="error-text">{error}</p> : null}
      <button className="primary" disabled={busy} onClick={submit}>
        {busy ? "Saving…" : "Create site"}
      </button>
    </div>
  );
}
