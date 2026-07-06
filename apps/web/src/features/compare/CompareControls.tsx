import { useCatalog } from "../../api/queries";
import type { Dataset } from "../../api/types";
import { useCompareStore, type SideConfig } from "../../stores/compareStore";

/** Floating control panel for the Compare view: mode, orientation, per-side. */
export function CompareControls() {
  const { data: catalog } = useCatalog();
  const mode = useCompareStore((s) => s.mode);
  const orientation = useCompareStore((s) => s.orientation);
  const left = useCompareStore((s) => s.left);
  const right = useCompareStore((s) => s.right);
  const setMode = useCompareStore((s) => s.setMode);
  const setOrientation = useCompareStore((s) => s.setOrientation);
  const setSide = useCompareStore((s) => s.setSide);
  const setShared = useCompareStore((s) => s.setShared);

  return (
    <div className="compare-controls">
      <div className="compare-controls-head">
        <div className="method-toggle">
          {(["linked", "independent"] as const).map((m) => (
            <button
              key={m}
              className={mode === m ? "toggle active" : "toggle"}
              onClick={() => setMode(m)}
            >
              {m === "linked" ? "Linked" : "Independent"}
            </button>
          ))}
        </div>
        <button
          className="mini"
          onClick={() => setOrientation(orientation === "vertical" ? "horizontal" : "vertical")}
          title="Swipe orientation"
        >
          {orientation === "vertical" ? "↔ Vertical" : "↕ Horizontal"}
        </button>
      </div>

      {mode === "linked" ? (
        <div className="compare-linked">
          <LayerPicker
            catalog={catalog}
            dataset={left.dataset}
            product={left.product}
            onChange={(dataset, product) => setShared({ dataset, product })}
          />
          <div className="compare-dates">
            <label>
              Left date
              <input
                type="date"
                value={left.date}
                onChange={(e) => setSide("left", { date: e.target.value })}
              />
            </label>
            <label>
              Right date
              <input
                type="date"
                value={right.date}
                onChange={(e) => setSide("right", { date: e.target.value })}
              />
            </label>
          </div>
        </div>
      ) : (
        <div className="compare-independent">
          <SidePanel catalog={catalog} side="left" config={left} onChange={setSide} />
          <SidePanel catalog={catalog} side="right" config={right} onChange={setSide} />
        </div>
      )}
    </div>
  );
}

function SidePanel({
  catalog,
  side,
  config,
  onChange,
}: {
  catalog: Dataset[] | undefined;
  side: "left" | "right";
  config: SideConfig;
  onChange: (side: "left" | "right", patch: Partial<SideConfig>) => void;
}) {
  return (
    <div className="compare-side-panel">
      <span className="compare-side-label">{side === "left" ? "Left (A)" : "Right (B)"}</span>
      <LayerPicker
        catalog={catalog}
        dataset={config.dataset}
        product={config.product}
        onChange={(dataset, product) => onChange(side, { dataset, product })}
      />
      <label>
        Date
        <input
          type="date"
          value={config.date}
          onChange={(e) => onChange(side, { date: e.target.value })}
        />
      </label>
    </div>
  );
}

function LayerPicker({
  catalog,
  dataset,
  product,
  onChange,
}: {
  catalog: Dataset[] | undefined;
  dataset: string;
  product: string;
  onChange: (dataset: string, product: string) => void;
}) {
  const current = catalog?.find((d) => d.id === dataset) ?? catalog?.[0];
  const products = current?.products.filter((p) => !p.requires_builder) ?? [];

  return (
    <div className="compare-layer-picker">
      <select
        value={current?.id ?? ""}
        onChange={(e) => {
          const next = catalog?.find((d) => d.id === e.target.value);
          const firstProduct = next?.products.find((p) => !p.requires_builder);
          onChange(e.target.value, firstProduct?.key ?? "");
        }}
      >
        {catalog?.map((d) => (
          <option key={d.id} value={d.id}>
            {d.title}
          </option>
        ))}
      </select>
      <select value={product} onChange={(e) => onChange(dataset, e.target.value)}>
        {products.map((p) => (
          <option key={p.key} value={p.key}>
            {p.name}
          </option>
        ))}
      </select>
    </div>
  );
}
