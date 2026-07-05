import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError, apiPost } from "../../api/client";
import type { Dataset } from "../../api/types";

const TEMPLATE = `[dataset]
id = "modis_lst"
title = "MODIS Land Surface Temperature"
collection_id = "MODIS/061/MOD11A1"
attribution = "NASA LP DAAC"
default_scale_m = 1000

[products.LST_DAY]
name = "LST (day)"
source_band = "LST_Day_1km"
vis_min = 13000.0
vis_max = 16500.0
valid_min = 7500.0
valid_max = 65535.0
display_unit = "K"
display_scale = 0.02
palette = ["#0b1d51", "#4462c8", "#7fd4e4", "#f8fcc1", "#f4a663", "#a30f0f"]
description = "1 km daytime land surface temperature (scale 0.02 K/DN)."
`;

export function CustomDatasetEditor() {
  const [toml, setToml] = useState("");
  const queryClient = useQueryClient();

  const create = useMutation({
    mutationFn: (body: string) => apiPost<Dataset>("/api/catalog/custom", { toml: body }),
    onSuccess: () => {
      setToml("");
      void queryClient.invalidateQueries({ queryKey: ["catalog"] });
    },
  });

  const errorDetail =
    create.error instanceof ApiError
      ? create.error.detail
      : create.error
        ? String(create.error)
        : null;

  return (
    <div className="dataset-editor">
      <p className="muted">
        Register any public GEE ImageCollection from TOML — no code changes. The definition is
        validated, persisted to <code>data/catalog.d/</code>, and appears in the catalog
        immediately.
      </p>
      <textarea
        rows={14}
        spellCheck={false}
        placeholder={TEMPLATE}
        value={toml}
        onChange={(event) => setToml(event.target.value)}
      />
      <div className="roi-buttons">
        <button onClick={() => setToml(TEMPLATE)}>Paste template</button>
        <button
          className="primary"
          disabled={!toml.trim() || create.isPending}
          onClick={() => create.mutate(toml)}
        >
          {create.isPending ? "Validating…" : "Add dataset"}
        </button>
      </div>
      {create.isSuccess ? <p className="success">Dataset registered.</p> : null}
      {errorDetail ? <p className="error-text">{errorDetail}</p> : null}
    </div>
  );
}
