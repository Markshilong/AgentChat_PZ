from prettytable import PrettyTable
from prettytable import TableStyle

from bdf_pz.catalog import build_catalog_for_dataset
from bdf_pz.db import persist_dataset_registration
from bdf_pz.db import preview_catalog
from bdf_pz.db import summarize_dataset_catalog

dataset_name = "{{ dataset_name }}".strip() or None
modality = "{{ modality }}".strip().lower() or None
limit = int({{ limit }})

def _has_materialized_metadata(df):
    metadata_columns = [
        "title",
        "abstract",
        "summary",
        "caption",
        "description",
        "ocr_text",
        "authors",
        "topics",
        "objects",
    ]
    present_columns = [column for column in metadata_columns if column in df.columns]
    if not present_columns:
        return False
    for column in present_columns:
        values = (
            df[column]
            .fillna("")
            .astype(str)
            .str.strip()
        )
        if (values != "").any():
            return True
    return False

preview_df = preview_catalog(dataset_name=dataset_name, modality=modality, limit=limit)
catalog_metrics = None
rebuild_summary = None

if dataset_name is not None:
    entry = _get_registered_dataset_entry(dataset_name)
    source_type = entry.get("source_type", "file_path")
    catalog_metrics = summarize_dataset_catalog(dataset_name)
    needs_rebuild = (
        source_type == "file_path"
        and (
            catalog_metrics["pending_asset_count"] > 0
            or not _has_materialized_metadata(preview_df)
        )
    )
    if needs_rebuild:
        rebuild_summary = build_catalog_for_dataset(
            dataset_name,
            entry["path"],
            source_type=source_type,
        )
        refreshed_metrics = summarize_dataset_catalog(dataset_name)
        entry["catalog_summary"] = {
            **rebuild_summary,
            "persisted": refreshed_metrics,
        }
        if refreshed_metrics["indexed_asset_count"] > 0 and refreshed_metrics["pending_asset_count"] == 0:
            entry["catalog_status"] = "ready"
        elif refreshed_metrics["error_asset_count"] > 0 or rebuild_summary["errors"]:
            entry["catalog_status"] = "partial"
        elif refreshed_metrics["pending_asset_count"] > 0:
            entry["catalog_status"] = "pending"
        else:
            entry["catalog_status"] = "registered_only"
        registered_datasets[dataset_name] = entry
        persist_dataset_registration(entry)
        preview_df = preview_catalog(dataset_name=dataset_name, modality=modality, limit=limit)
        catalog_metrics = refreshed_metrics

if preview_df.empty:
    if dataset_name:
        output = f"No catalog rows found for dataset '{dataset_name}'."
    else:
        output = "No catalog rows found."
else:
    table = PrettyTable(list(preview_df.columns))
    table.add_rows(preview_df.astype(str).values.tolist())
    table.set_style(TableStyle.MARKDOWN)
    output = table.get_string()
    if dataset_name and not _has_materialized_metadata(preview_df):
        note = []
        if catalog_metrics is not None:
            note.append(
                "Catalog metadata is still incomplete "
                f"(indexed={catalog_metrics['indexed_asset_count']}, "
                f"pending={catalog_metrics['pending_asset_count']}, "
                f"errors={catalog_metrics['error_asset_count']})."
            )
        if rebuild_summary and rebuild_summary.get("errors"):
            note.append("Latest rebuild errors: " + "; ".join(rebuild_summary["errors"]))
        if note:
            output += "\n\n" + " ".join(note)

print(output)
output
