import palimpzest as pz

from bdf_pz.db import cache_extraction_dataframe, persist_dataset_registration, write_dataframe
from bdf_pz.model_selection import get_query_available_models

dataset_name = "{{ dataset_name }}".strip()
policy_method = "{{ policy_method }}".strip() or "max_quality"
allow_code_synth = {{ allow_code_synth }}

entry = _get_registered_dataset_entry(dataset_name)
if entry.get("source_type") != "multimodal":
    raise ValueError(
        f"Dataset '{dataset_name}' is not a multimodal dataset "
        f"(source_type={entry.get('source_type')!r})."
    )

if policy_method == "min_cost":
    policy = pz.MinCost()
elif policy_method == "max_quality":
    policy = pz.MaxQuality()
else:
    raise ValueError(f"Unsupported policy_method: {policy_method}")

config = pz.QueryProcessorConfig(
    policy=policy,
    cache=False,
    verbose=True,
    progress=True,
    available_models=get_query_available_models(),
    allow_code_synth=allow_code_synth,
)

modalities = entry["modalities"]
extracted_parts = []
skipped_parts = []
failed_parts = []

for kind, info in modalities.items():
    if info.get("status") == "skipped":
        skipped_parts.append(f"{kind} ({info.get('reason', 'no schema')})")
        continue

    schema_name = info["schema"]
    schema = existing_schemas[schema_name]
    sub_path = info["path"]
    table_name = f"{dataset_name}_{kind}_results"

    try:
        sub_dataset = _construct_dataset_from_path(sub_path, f"{dataset_name}_{kind}")

        # Per-modality depends_on: sem_add_columns doesn't validate depends_on at chain
        # time (errors only fire inside run()), so a try-cascade is unsafe. Pick the
        # right field up front per modality instead.
        if kind in {"image", "audio"}:
            depends_on = None
        elif kind == "text":
            depends_on = ["contents"]
        elif kind == "html":
            depends_on = ["text"]
        else:  # pdf, html, xls — text derived from a non-text source
            depends_on = ["text_contents"]

        if depends_on is None:
            applied = sub_dataset.sem_add_columns(schema)
        else:
            applied = sub_dataset.sem_add_columns(schema, depends_on=depends_on)

        results = applied.run(config)
        results_df = results.to_df()

        # Drop heavy columns before persisting so downstream tool reads (preview_results,
        # register_query_dataset, retrieve_dataset) don't push raw file payloads back into
        # the agent's conversation context. Mirrors the projection in execute_workload.py.
        heavy_columns = {"contents", "text_contents", "html", "text"}
        compact_columns = [c for c in results_df.columns if c not in heavy_columns]
        compact_df = results_df[compact_columns].copy() if compact_columns else results_df.copy()

        saved = write_dataframe(compact_df, table_name, if_exists="replace")
        cache_summary = cache_extraction_dataframe(
            dataset_name=dataset_name,
            schema_name=schema_name,
            df=compact_df,
            source_output_dataset=table_name,
        )

        info["results_table"] = saved
        info["row_count"] = int(len(compact_df))
        info["status"] = "extracted"
        info["cache_summary"] = cache_summary
        extracted_parts.append(f"{kind}: {len(compact_df)} rows -> {saved}")
    except Exception as exc:
        info["status"] = "failed"
        info["error"] = f"{type(exc).__name__}: {exc}"
        info.pop("results_table", None)
        failed_parts.append(f"{kind}: {type(exc).__name__}: {exc}")

registered_datasets[dataset_name] = entry
cached_schemas = list(entry.get("cached_schemas", []))
for kind, info in modalities.items():
    schema_name = info.get("schema")
    if info.get("status") == "extracted" and schema_name and schema_name not in cached_schemas:
        cached_schemas.append(schema_name)
if cached_schemas:
    entry["cached_schemas"] = sorted(cached_schemas)
    registered_datasets[dataset_name] = entry
persist_dataset_registration(registered_datasets[dataset_name])

if extracted_parts:
    output = "Multimodal extraction: " + "; ".join(extracted_parts)
else:
    output = "Multimodal extraction produced no results"
if failed_parts:
    output += ". Failures: " + "; ".join(failed_parts)
if skipped_parts:
    output += ". Skipped: " + ", ".join(skipped_parts)
print(output)
output
