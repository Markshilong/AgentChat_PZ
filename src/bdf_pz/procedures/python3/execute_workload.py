import faulthandler
import os
import palimpzest as pz
import traceback

from bdf_pz.db import cache_extraction_dataframe, persist_dataset_registration, write_dataframe
from bdf_pz.model_selection import get_query_available_models

output_dataset_name = "{{ output_dataset }}"
debug_log_path = "/tmp/bdf_pz_execute_workload.log"
results_path = "/tmp/bdf_pz_execute_workload_results.csv"
result_sink_type = "{{ result_sink_type }}".strip().lower() or "duckdb"
result_sink_target = "{{ result_sink_target }}".strip()

log_file = open(debug_log_path, "a", encoding="utf-8", buffering=1)

def _log(message):
    print(message, file=log_file, flush=True)

_log("=== execute_workload start ===")
_log(f"output_dataset_name={output_dataset_name}")
_log(f"result_sink_type={result_sink_type}")
_log(f"result_sink_target={result_sink_target}")

if "dataset" not in locals():
    output = output_dataset_name
else:
    output = dataset

assert isinstance(output, pz.Dataset), "Output should be a Dataset object"
_log(f"dataset_type={type(output)}")

policy_method = "{{ policy_method }}"

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
    allow_code_synth={{ allow_code_synth }},
)
_log(f"config={config}")

faulthandler.enable(file=log_file, all_threads=True)
faulthandler.dump_traceback_later(30, repeat=True, file=log_file)

try:
    _log("calling output.run(config)")
    results = output.run(config)
    _log("output.run(config) returned")
except Exception as exc:
    output = f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}"
    _log(output)
    print(output)
finally:
    faulthandler.cancel_dump_traceback_later()

if "results" in locals():
    _log("calling results.to_df()")
    results_df = results.to_df()
    _log("results.to_df() returned")
    schema_name = globals().get("current_output_schema_name")
    if not schema_name:
        schema_name = getattr(getattr(output, "schema", None), "__name__", None)
    if not schema_name:
        schema_name = output_dataset_name
    preferred_columns = [
        "filename",
        "paper_title",
        "author",
        "author_name",
        "authors",
        "title",
        "first_author",
        "year",
        "abstract",
        "name",
        "description",
        "url",
        "public_url",
        "index",
    ]
    heavy_columns = {"contents", "text_contents", "html", "text"}
    non_heavy_columns = [
        column for column in results_df.columns if column not in heavy_columns
    ]
    preferred_present = [
        column for column in preferred_columns if column in non_heavy_columns
    ]
    remaining_columns = [
        column for column in non_heavy_columns if column not in preferred_present
    ]
    compact_columns = preferred_present + remaining_columns
    compact_df = (
        results_df[compact_columns].copy()
        if compact_columns
        else results_df.copy()
    )

    if result_sink_type == "duckdb":
        table_name = result_sink_target or f"{output_dataset_name}_results"
        saved_target = write_dataframe(compact_df, table_name, if_exists="replace")
        output = (
            f"Execution complete: {len(compact_df)} rows written to DuckDB table "
            f"{saved_target}"
        )
    elif result_sink_type == "csv":
        saved_target = result_sink_target or results_path
        compact_df.to_csv(saved_target, index=False)
        output = (
            f"Execution complete: {len(compact_df)} rows written to CSV "
            f"{saved_target}"
        )
    else:
        raise ValueError(
            f"Unsupported result_sink_type: {result_sink_type}. "
            "Supported values are 'duckdb' and 'csv'."
        )

    cache_summary = None
    current_input_dataset_name = globals().get("current_input_dataset_name")
    if current_input_dataset_name:
        try:
            cache_summary = cache_extraction_dataframe(
                dataset_name=current_input_dataset_name,
                schema_name=schema_name,
                df=compact_df,
                source_output_dataset=output_dataset_name,
            )
            if current_input_dataset_name in registered_datasets:
                entry = _get_registered_dataset_entry(current_input_dataset_name)
                cached_schemas = list(entry.get("cached_schemas", []))
                if schema_name not in cached_schemas:
                    cached_schemas.append(schema_name)
                entry["cached_schemas"] = sorted(cached_schemas)
                entry["last_cached_schema"] = schema_name
                entry["last_cache_summary"] = cache_summary
                registered_datasets[current_input_dataset_name] = entry
                persist_dataset_registration(entry)
        except Exception as exc:
            cache_summary = {
                "schema_name": schema_name,
                "error": f"{type(exc).__name__}: {exc}",
            }
            _log(f"cache_error={cache_summary['error']}")

    if cache_summary and cache_summary.get("cached_rows"):
        output += (
            f"; schema cache '{schema_name}' updated for "
            f"{cache_summary['cached_rows']} row(s)"
        )

    last_result_sink = {
        "sink_type": result_sink_type,
        "target": saved_target,
        "row_count": len(compact_df),
        "columns": list(compact_df.columns),
        "source_output_dataset": output_dataset_name,
        "schema_name": schema_name,
        "cache_summary": cache_summary,
    }
    _log(f"last_result_sink={last_result_sink}")
    _log("=== execute_workload success ===")
    print(output)

output
