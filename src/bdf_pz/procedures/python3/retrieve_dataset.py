import pandas as pd
from prettytable import PrettyTable
from prettytable import TableStyle

dataset_name = "{{ dataset_name }}"
entry = _get_registered_dataset_entry(dataset_name)
source_type = entry.get("source_type", "file_path")

if source_type == "file_path":
    dataset_path = entry["path"]
    catalog_status = entry.get("catalog_status")
    output = None
    if catalog_status in {"ready", "partial"}:
        from bdf_pz.db import preview_catalog

        preview_df = preview_catalog(dataset_name=dataset_name, limit=20)
        if not preview_df.empty:
            table = PrettyTable(list(preview_df.columns))
            table.add_rows(preview_df.astype(str).values.tolist())
            table.set_style(TableStyle.MARKDOWN)
            output = table.get_string()

    if output is None:
        if os.path.isdir(dataset_path):
            output = os.listdir(dataset_path)
        else:
            output = [os.path.basename(dataset_path)]
elif source_type == "multimodal":
    rows = []
    for kind, info in entry.get("modalities", {}).items():
        results_table = info.get("results_table") or "-"
        status = info.get("status", "registered")
        rows.append([
            kind,
            info.get("schema") or "-",
            info.get("count", 0),
            status,
            results_table,
        ])
    if not rows:
        output = f"Multimodal dataset '{dataset_name}' has no modalities recorded."
    else:
        table = PrettyTable(["Modality", "Schema", "N. Files", "Status", "Results Table"])
        table.add_rows(rows)
        table.set_style(TableStyle.MARKDOWN)
        output = table.get_string()
else:
    connection_name = entry.get("connection_name", "local_duckdb")
    if connection_name not in {"local_duckdb", "duckdb", ""}:
        raise ValueError(
            f"Unsupported connection_name for preview: {connection_name}. "
            "The MVP currently supports DuckDB-backed structured datasets."
        )

    from bdf_pz.db import preview_query, preview_table

    if source_type == "sql_table":
        preview_df = preview_table(entry["table_name"], limit=50)
    elif source_type == "sql_query":
        preview_df = preview_query(entry["sql_query"], limit=50)
    else:
        raise ValueError(f"Unsupported source_type: {source_type}")

    if preview_df.empty:
        output = f"No rows found in dataset '{dataset_name}'."
    else:
        table = PrettyTable(list(preview_df.columns))
        table.add_rows(preview_df.astype(str).values.tolist())
        table.set_style(TableStyle.MARKDOWN)
        output = table.get_string()

print(output)
output
