import os
import shutil

from bdf_pz.catalog import build_catalog_for_dataset
from bdf_pz.db import persist_dataset_registration, summarize_dataset_catalog

path = "{{ path }}".strip()
name = "{{ name }}".strip()

if not os.path.isdir(path):
    raise ValueError(f"Path is not a directory: {path}")

path = os.path.abspath(path)

groups = _partition_files_by_kind(path)
unknown_files = [n for n, _ in groups.pop("_unknown", [])]

if not groups:
    raise ValueError(f"No recognized files found in {path}")

# Single-modality fallback: register as a normal file-backed dataset.
if len(groups) == 1:
    only_kind = next(iter(groups))
    registered_datasets[name] = {
        "name": name,
        "source_type": "file_path",
        "path": path,
        "catalog_enabled": True,
        "cached_schemas": [],
    }
    try:
        catalog_summary = build_catalog_for_dataset(name, path, source_type="file_path")
        registered_datasets[name]["catalog_summary"] = catalog_summary
        persisted_summary = summarize_dataset_catalog(name)
        if persisted_summary["indexed_asset_count"] > 0 and persisted_summary["pending_asset_count"] == 0:
            registered_datasets[name]["catalog_status"] = "ready"
        elif persisted_summary["error_asset_count"] > 0 or catalog_summary["errors"]:
            registered_datasets[name]["catalog_status"] = "partial"
        elif persisted_summary["pending_asset_count"] > 0:
            registered_datasets[name]["catalog_status"] = "pending"
        else:
            registered_datasets[name]["catalog_status"] = "registered_only"
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
        registered_datasets[name]["catalog_status"] = "error"
        registered_datasets[name]["catalog_error"] = f"{type(exc).__name__}: {exc}"
        catalog_note = f"; catalog setup deferred: {type(exc).__name__}: {exc}"
    output = (
        f"Only one modality ({only_kind}) found; registered '{name}' as a normal "
        f"file-backed dataset at {path}{catalog_note}"
    )
    persist_dataset_registration(registered_datasets[name])
    if unknown_files:
        output += f" (skipped {len(unknown_files)} files with unrecognized extensions)"
    print(output)
else:
    materialized_root = os.path.join("/tmp", "bdf_pz_multimodal", name)
    if os.path.exists(materialized_root):
        shutil.rmtree(materialized_root)
    os.makedirs(materialized_root, exist_ok=True)

    modalities = {}
    summary_parts = []
    skipped_parts = []
    for kind, files in groups.items():
        kind_dir = os.path.join(materialized_root, kind)
        os.makedirs(kind_dir, exist_ok=True)
        for filename, source_path in files:
            os.symlink(source_path, os.path.join(kind_dir, filename))

        schema_name = _default_schema_for_kind.get(kind)
        if schema_name is None:
            modalities[kind] = {
                "path": kind_dir,
                "count": len(files),
                "schema": None,
                "status": "skipped",
                "reason": f"no default schema for modality '{kind}'",
            }
            skipped_parts.append(f"{kind} ({len(files)} files, no default schema)")
        else:
            modalities[kind] = {
                "path": kind_dir,
                "count": len(files),
                "schema": schema_name,
                "status": "registered",
            }
            summary_parts.append(f"{kind}: {len(files)} files -> {schema_name}")

    registered_datasets[name] = {
        "name": name,
        "source_type": "multimodal",
        "path": path,
        "materialized_root": materialized_root,
        "modalities": modalities,
        "catalog_enabled": True,
        "cached_schemas": [],
    }

    catalog_indexed = 0
    catalog_reused = 0
    catalog_failed = 0
    catalog_errors = []
    catalog_modalities = []
    for kind, info in modalities.items():
        if info.get("status") == "skipped":
            continue
        try:
            catalog_summary = build_catalog_for_dataset(
                name,
                info["path"],
                source_type="multimodal",
            )
            info["catalog_summary"] = catalog_summary
            info["catalog_status"] = (
                "ready" if not catalog_summary["errors"] else "partial"
            )
            catalog_indexed += catalog_summary["indexed_assets"]
            catalog_reused += catalog_summary["reused_assets"]
            catalog_failed += catalog_summary["failed_assets"]
            catalog_errors.extend(catalog_summary["errors"])
            catalog_modalities.append(kind)
        except Exception as exc:
            info["catalog_status"] = "error"
            info["catalog_error"] = f"{type(exc).__name__}: {exc}"
            catalog_errors.append(f"{kind}: {type(exc).__name__}: {exc}")

    registered_datasets[name]["modalities"] = modalities
    registered_datasets[name]["catalog_modalities"] = sorted(catalog_modalities)
    persisted_summary = summarize_dataset_catalog(name)
    if persisted_summary["indexed_asset_count"] > 0 and persisted_summary["pending_asset_count"] == 0:
        registered_datasets[name]["catalog_status"] = "ready"
    elif persisted_summary["error_asset_count"] > 0 or catalog_errors:
        registered_datasets[name]["catalog_status"] = "partial"
    elif persisted_summary["pending_asset_count"] > 0:
        registered_datasets[name]["catalog_status"] = "pending"
    else:
        registered_datasets[name]["catalog_status"] = "registered_only"
    registered_datasets[name]["catalog_summary"] = {
        "indexed_assets": catalog_indexed,
        "reused_assets": catalog_reused,
        "failed_assets": catalog_failed,
        "errors": catalog_errors,
        "persisted": persisted_summary,
    }

    output = f"Registered multimodal dataset '{name}' with " + ", ".join(summary_parts)
    if skipped_parts:
        output += ". Skipped: " + ", ".join(skipped_parts)
    if unknown_files:
        output += f". Ignored {len(unknown_files)} files with unrecognized extensions."
    output += (
        f". Catalog: {persisted_summary['indexed_asset_count']} indexed, {catalog_reused} reused"
    )
    if persisted_summary["pending_asset_count"]:
        output += f", {persisted_summary['pending_asset_count']} pending"
    if catalog_failed or persisted_summary["error_asset_count"]:
        output += f", {max(catalog_failed, persisted_summary['error_asset_count'])} failed"
    persist_dataset_registration(registered_datasets[name])
    print(output)

output
