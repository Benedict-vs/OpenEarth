import { useState } from "react";
import { useCatalog } from "../../api/queries";
import { useDateStore } from "../../stores/dateStore";
import type { RefWindow } from "../../stores/layersStore";
import { useLayersStore } from "../../stores/layersStore";

/** Default reference (pre) window: a month ending ~2 months before the post window. */
function defaultRefWindow(postStart: string): RefWindow {
  const start = new Date(`${postStart}T00:00:00Z`);
  const iso = (d: Date) => d.toISOString().slice(0, 10);
  const end = new Date(start);
  end.setUTCDate(end.getUTCDate() - 60);
  const begin = new Date(end);
  begin.setUTCDate(begin.getUTCDate() - 30);
  return { start: iso(begin), end: iso(end) };
}

export function CatalogBrowser() {
  const { data: catalog, isLoading, error } = useCatalog();
  const addLayer = useLayersStore((state) => state.addLayer);
  const postStart = useDateStore((state) => state.start);
  const [datasetId, setDatasetId] = useState<string>("s2");
  const [productKey, setProductKey] = useState<string>("");

  if (isLoading) return <p className="muted">Loading catalog…</p>;
  if (error || !catalog) return <p className="muted">Catalog unavailable — is the API running?</p>;

  const dataset = catalog.find((d) => d.id === datasetId) ?? catalog[0];
  if (!dataset) return <p className="muted">Catalog is empty.</p>;

  const product = dataset.products.find((p) => p.key === productKey) ?? dataset.products[0] ?? null;

  return (
    <div className="catalog-browser">
      <label>
        Dataset
        <select
          value={dataset.id}
          onChange={(event) => {
            setDatasetId(event.target.value);
            setProductKey("");
          }}
        >
          {catalog.map((d) => (
            <option key={d.id} value={d.id}>
              {d.title}
              {d.is_custom ? " (custom)" : ""}
            </option>
          ))}
        </select>
      </label>
      <label>
        Product
        <select value={product?.key ?? ""} onChange={(event) => setProductKey(event.target.value)}>
          {dataset.products.map((p) => (
            <option key={p.key} value={p.key} disabled={p.requires_builder}>
              {p.name}
              {p.requires_builder ? " (Phase 3)" : ""}
            </option>
          ))}
        </select>
      </label>
      {product?.description ? (
        // Catalog descriptions carry light markdown bold markers; render as plain text.
        <p className="muted product-description">{product.description.replaceAll("**", "")}</p>
      ) : null}
      <button
        className="primary"
        disabled={!product || product.requires_builder}
        title={
          product?.requires_builder ? "Needs the dedicated methane pipeline (Phase 3)." : undefined
        }
        onClick={() => {
          if (!product) return;
          addLayer(dataset.id, product.key, `${dataset.title} · ${product.name}`, {
            needsRef: product.needs_ref,
            ref: product.needs_ref ? defaultRefWindow(postStart) : null,
          });
        }}
      >
        Add layer
      </button>
    </div>
  );
}
