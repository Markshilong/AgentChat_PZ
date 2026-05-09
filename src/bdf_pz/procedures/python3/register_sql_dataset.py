connection_name = "{{ connection_name }}".strip() or "local_duckdb"
table_name = "{{ table_name }}".strip()
dataset_name = "{{ dataset_name }}".strip()

if connection_name not in {"local_duckdb", "duckdb"}:
    raise ValueError(
        f"Unsupported connection_name: {connection_name}. "
        "The MVP currently supports DuckDB-backed structured datasets."
    )

from bdf_pz.db import list_tables
from bdf_pz.db import persist_dataset_registration
from bdf_pz.db import register_structured_dataset

if table_name not in list_tables():
    raise ValueError(f"Table does not exist in DuckDB: {table_name}")

registered_datasets[dataset_name] = {
    "name": dataset_name,
    "source_type": "sql_table",
    "connection_name": connection_name,
    "table_name": table_name,
    "catalog_enabled": True,
    "catalog_status": "structured_registered",
    "cached_schemas": [],
}

register_structured_dataset(
    dataset_name=dataset_name,
    connection_name=connection_name,
    source_type="sql_table",
    target_name=table_name,
    metadata={"registered_via": "register_sql_dataset"},
)
persist_dataset_registration(registered_datasets[dataset_name])

output = (
    f"Registered SQL dataset '{dataset_name}' from "
    f"{connection_name}.{table_name}"
)
print(output)
output
