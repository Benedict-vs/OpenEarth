import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiDelete } from "../../api/client";
import { useMlStatus } from "../../api/methaneQueries";
import { useCatalog, useConfig } from "../../api/queries";
import { CustomDatasetEditor } from "./CustomDatasetEditor";

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KiB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MiB`;
  return `${(bytes / 1024 ** 3).toFixed(2)} GiB`;
}

function EeStatus() {
  const { data: config } = useConfig();
  if (!config) return <p className="muted">Loading…</p>;
  return (
    <dl className="config-list">
      <dt>Earth Engine</dt>
      <dd>
        {config.ee_initialized ? (
          <span className="status-ok">initialized</span>
        ) : (
          <span className="error-text">{config.ee_error ?? "not initialized"}</span>
        )}
      </dd>
      <dt>EE project</dt>
      <dd>{config.ee_project ?? "—"}</dd>
      <dt>Tile TTL</dt>
      <dd>{(config.tile_ttl_seconds / 3600).toFixed(1)} h</dd>
      <dt>Data dir</dt>
      <dd>
        <code>{config.data_dir}</code>
      </dd>
      <dt>Cache</dt>
      <dd>
        {config.cache.count} entries · {formatBytes(config.cache.volume_bytes)}
      </dd>
      <dt>API version</dt>
      <dd>{config.version}</dd>
    </dl>
  );
}

function MlModelStatus() {
  const { data: status } = useMlStatus();
  if (!status) return <p className="muted">Loading…</p>;
  return (
    <>
      <dl className="config-list">
        <dt>Model</dt>
        <dd>
          {status.model_loaded ? (
            <span className="status-ok">installed</span>
          ) : (
            <span className="error-text">not installed</span>
          )}
        </dd>
        {status.model_loaded ? (
          <>
            <dt>Version</dt>
            <dd>{status.model_version ?? "—"}</dd>
            <dt>Latency (p50)</dt>
            <dd>
              {status.latency_ms_p50 != null ? `${status.latency_ms_p50.toFixed(1)} ms/chip` : "—"}
            </dd>
          </>
        ) : null}
      </dl>
      <p className="muted">
        Candidate ranker for the Methane Lab — proposes scenes for human review, never an autonomous
        detector. {status.model_loaded ? "" : "Install the ONNX model under data_dir/ml/models/."}
      </p>
    </>
  );
}

function CustomDatasetList() {
  const { data: catalog } = useCatalog();
  const queryClient = useQueryClient();
  const remove = useMutation({
    mutationFn: (id: string) => apiDelete(`/api/catalog/custom/${id}`),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["catalog"] }),
  });

  const custom = catalog?.filter((d) => d.is_custom) ?? [];
  if (custom.length === 0) return <p className="muted">No custom datasets yet.</p>;
  return (
    <ul className="custom-dataset-list">
      {custom.map((dataset) => (
        <li key={dataset.id}>
          <span>
            <strong>{dataset.title}</strong>{" "}
            <span className="muted">
              {dataset.id} · {dataset.collection_id} · {dataset.products.length} product(s)
            </span>
          </span>
          <button
            title="Delete this custom dataset"
            disabled={remove.isPending}
            onClick={() => remove.mutate(dataset.id)}
          >
            Delete
          </button>
        </li>
      ))}
    </ul>
  );
}

export function SettingsPage() {
  return (
    <div className="settings-page">
      <div className="panel-section">
        <h3>Status</h3>
        <EeStatus />
      </div>
      <div className="panel-section">
        <h3>ML tier</h3>
        <MlModelStatus />
      </div>
      <div className="panel-section">
        <h3>Custom datasets</h3>
        <CustomDatasetList />
      </div>
      <div className="panel-section">
        <h3>Add a dataset (TOML)</h3>
        <CustomDatasetEditor />
      </div>
    </div>
  );
}
