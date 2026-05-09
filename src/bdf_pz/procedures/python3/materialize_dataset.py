import os
import shutil

from bdf_pz.catalog import build_catalog_for_dataset
from bdf_pz.db import get_duckdb_connection, persist_dataset_registration, summarize_dataset_catalog

dataset_name = "{{ dataset_name }}".strip()
path_column = "{{ path_column }}".strip()
output_dataset_name = "{{ output_dataset_name }}".strip()

entry = _get_registered_dataset_entry(dataset_name)
source_type = entry.get("source_type", "file_path")

if source_type not in {"sql_table", "sql_query"}:
    raise ValueError(
        f"Dataset '{dataset_name}' has source_type '{source_type}'. "
        "materialize_dataset currently supports only SQL-backed datasets."
    )

connection_name = entry.get("connection_name", "local_duckdb")
if connection_name not in {"local_duckdb", "duckdb", ""}:
    raise ValueError(
        f"Unsupported connection_name: {connection_name}. "
        "The MVP currently supports DuckDB-backed structured datasets."
    )

conn = get_duckdb_connection(read_only=True)
try:
    if source_type == "sql_table":
        table_name = entry["table_name"]
        df = conn.execute(
            f'SELECT "{path_column}" FROM "{table_name}"'
        ).fetch_df()
    else:
        sql_query = entry["sql_query"]
        df = conn.execute(
            f'SELECT "{path_column}" FROM ({sql_query}) AS materialized_query'
        ).fetch_df()
finally:
    conn.close()

if path_column not in df.columns:
    raise ValueError(
        f"Column '{path_column}' not found in dataset '{dataset_name}'. "
        f"Available columns: {', '.join(df.columns)}"
    )

paths = []
for raw_value in df[path_column].tolist():
    if raw_value is None:
        continue
    path = str(raw_value).strip()
    if not path:
        continue
    paths.append(path)

if not paths:
    raise ValueError(
        f"No usable file paths found in column '{path_column}' for dataset '{dataset_name}'."
    )

materialized_root = os.path.join("/tmp", "bdf_pz_materialized", output_dataset_name)
if os.path.exists(materialized_root):
    shutil.rmtree(materialized_root)
os.makedirs(materialized_root, exist_ok=True)

linked_count = 0
missing_paths = []
for idx, source_path in enumerate(paths):
    if not os.path.exists(source_path):
        missing_paths.append(source_path)
        continue

    basename = os.path.basename(source_path)
    target_name = f"{idx:04d}_{basename}"
    target_path = os.path.join(materialized_root, target_name)
    os.symlink(source_path, target_path)
    linked_count += 1

if linked_count == 0:
    raise ValueError(
        f"None of the paths in column '{path_column}' could be materialized. "
        f"First missing path: {missing_paths[0] if missing_paths else 'unknown'}"
    )

registered_datasets[output_dataset_name] = {
    "name": output_dataset_name,
    "source_type": "file_path",
    "path": materialized_root,
    "materialized_from": dataset_name,
    "path_column": path_column,
    "catalog_enabled": True,
    "cached_schemas": [],
}

catalog_note = ""
try:
    catalog_summary = build_catalog_for_dataset(
        output_dataset_name,
        materialized_root,
        source_type="materialized_file_path",
    )
    persisted_summary = summarize_dataset_catalog(output_dataset_name)
    registered_datasets[output_dataset_name]["catalog_summary"] = {
        **catalog_summary,
        "persisted": persisted_summary,
    }
    if persisted_summary["indexed_asset_count"] > 0 and persisted_summary["pending_asset_count"] == 0:
        registered_datasets[output_dataset_name]["catalog_status"] = "ready"
    elif persisted_summary["error_asset_count"] > 0 or catalog_summary["errors"]:
        registered_datasets[output_dataset_name]["catalog_status"] = "partial"
    elif persisted_summary["pending_asset_count"] > 0:
        registered_datasets[output_dataset_name]["catalog_status"] = "pending"
    else:
        registered_datasets[output_dataset_name]["catalog_status"] = "registered_only"
    registered_datasets[output_dataset_name]["catalog_modalities"] = sorted(
        catalog_summary["modalities"].keys()
    )
    catalog_note = (
        f"; catalog {persisted_summary['indexed_asset_count']} indexed, "
        f"{catalog_summary['reused_assets']} reused"
    )
    if persisted_summary["pending_asset_count"]:
        catalog_note += f", {persisted_summary['pending_asset_count']} pending"
    if catalog_summary["failed_assets"] or persisted_summary["error_asset_count"]:
        catalog_note += (
            f", {max(catalog_summary['failed_assets'], persisted_summary['error_asset_count'])} failed"
        )
except Exception as exc:
    registered_datasets[output_dataset_name]["catalog_status"] = "error"
    registered_datasets[output_dataset_name]["catalog_error"] = (
        f"{type(exc).__name__}: {exc}"
    )
    catalog_note = f"; catalog setup deferred: {type(exc).__name__}: {exc}"

persist_dataset_registration(registered_datasets[output_dataset_name])

missing_note = ""
if missing_paths:
    missing_note = f" ({len(missing_paths)} missing paths skipped)"

output = (
    f"Materialized dataset '{output_dataset_name}' at {materialized_root} "
    f"with {linked_count} files from column '{path_column}'{missing_note}{catalog_note}"
)
print(output)
output
