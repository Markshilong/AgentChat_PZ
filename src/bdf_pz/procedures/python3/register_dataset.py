import os

from bdf_pz.catalog import build_catalog_for_dataset
from bdf_pz.db import (
    import_delimited_file,
    persist_dataset_registration,
    register_structured_dataset,
    summarize_dataset_catalog,
)

path = "{{ path }}".strip()
name = "{{ name }}".strip()

if not os.path.exists(path):
    raise Exception(
        f"Path {path} is invalid. Does not point to a file or directory."
    )

path = os.path.abspath(path)
extension = os.path.splitext(path)[1].lower()

if os.path.isfile(path) and extension in {".csv", ".tsv"}:
    table_name = import_delimited_file(path, name, if_exists="replace")
    register_structured_dataset(
        dataset_name=name,
        connection_name="local_duckdb",
        source_type="sql_table",
        target_name=table_name,
        metadata={"imported_from": path, "file_extension": extension},
    )
    registered_datasets[name] = {
        "name": name,
        "source_type": "sql_table",
        "connection_name": "local_duckdb",
        "table_name": table_name,
        "imported_from": path,
        "catalog_enabled": True,
        "catalog_status": "structured_imported",
        "cached_schemas": [],
    }
    persist_dataset_registration(registered_datasets[name])
    output = (
        f"Registered structured dataset '{name}' from {path} into DuckDB table "
        f"{table_name}"
    )
else:
    registered_datasets[name] = {
        "name": name,
        "source_type": "file_path",
        "path": path,
        "catalog_enabled": True,
        "cached_schemas": [],
    }

    catalog_summary = None
    catalog_note = ""
    try:
        catalog_summary = build_catalog_for_dataset(name, path, source_type="file_path")
        persisted_summary = summarize_dataset_catalog(name)
        registered_datasets[name]["catalog_summary"] = {
            **catalog_summary,
            "persisted": persisted_summary,
        }
        if persisted_summary["indexed_asset_count"] > 0 and persisted_summary["pending_asset_count"] == 0:
            catalog_status = "ready"
        elif persisted_summary["error_asset_count"] > 0 or catalog_summary["errors"]:
            catalog_status = "partial"
        elif persisted_summary["pending_asset_count"] > 0:
            catalog_status = "pending"
        else:
            catalog_status = "registered_only"
        registered_datasets[name]["catalog_status"] = catalog_status
        registered_datasets[name]["catalog_modalities"] = sorted(
            catalog_summary["modalities"].keys()
        )
        catalog_note = (
            f". Catalog: {persisted_summary['indexed_asset_count']} indexed, "
            f"{catalog_summary['reused_assets']} reused"
        )
        if persisted_summary["pending_asset_count"]:
            catalog_note += f", {persisted_summary['pending_asset_count']} pending"
        if catalog_summary["failed_assets"] or persisted_summary["error_asset_count"]:
            catalog_note += (
                f", {max(catalog_summary['failed_assets'], persisted_summary['error_asset_count'])} failed"
            )
        if catalog_summary["skipped_assets"]:
            catalog_note += f", {catalog_summary['skipped_assets']} skipped"
    except Exception as exc:
        registered_datasets[name]["catalog_status"] = "error"
        registered_datasets[name]["catalog_error"] = f"{type(exc).__name__}: {exc}"
        catalog_note = f". Catalog setup deferred: {type(exc).__name__}: {exc}"

    persist_dataset_registration(registered_datasets[name])

    output = f"Registered dataset '{name}' at {path}{catalog_note}"

print(output)
output
