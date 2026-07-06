import { useRef, useState } from "react";
import { useImportValidation, useValidationEvents } from "../../api/methaneQueries";

/** Reference-event import (file + source) and the imported-events table. */
export function ValidationPanel() {
  const { data: events } = useValidationEvents();
  const importEvents = useImportValidation();
  const fileRef = useRef<HTMLInputElement>(null);
  const [source, setSource] = useState("imeo");

  const doImport = () => {
    const file = fileRef.current?.files?.[0];
    if (!file) return;
    const fmt = file.name.endsWith(".geojson") || file.name.endsWith(".json") ? "geojson" : "csv";
    importEvents.mutate(
      { file, source, fmt },
      { onSuccess: () => fileRef.current && (fileRef.current.value = "") },
    );
  };

  return (
    <details className="validation-panel">
      <summary>Reference events ({events?.length ?? 0})</summary>
      <div className="import-row">
        <input ref={fileRef} type="file" accept=".csv,.geojson,.json" />
        <select value={source} onChange={(e) => setSource(e.target.value)}>
          <option value="imeo">IMEO</option>
          <option value="sron">SRON</option>
          <option value="manual">Manual</option>
        </select>
        <button className="mini" onClick={doImport} disabled={importEvents.isPending}>
          Import
        </button>
      </div>
      {importEvents.data ? (
        <p className="muted">
          Imported {importEvents.data.imported}, skipped {importEvents.data.skipped}.
        </p>
      ) : null}
      {events && events.length > 0 ? (
        <ul className="event-list">
          {events.slice(0, 20).map((ev) => (
            <li key={ev.id}>
              <span>{ev.event_time_utc.slice(0, 10)}</span>
              <span className="muted">
                {ev.lat.toFixed(2)}, {ev.lon.toFixed(2)}
              </span>
              <span className="event-source">{ev.source}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </details>
  );
}
