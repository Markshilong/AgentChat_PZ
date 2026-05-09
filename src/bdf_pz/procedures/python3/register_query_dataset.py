connection_name = "{{ connection_name }}".strip() or "local_duckdb"
sql_query = """{{ sql_query }}""".strip()
dataset_name = "{{ dataset_name }}".strip()

if connection_name not in {"local_duckdb", "duckdb"}:
    raise ValueError(
        f"Unsupported connection_name: {connection_name}. "
        "The MVP currently supports DuckDB-backed structured datasets."
    )

from bdf_pz.db import preview_query
from bdf_pz.db import persist_dataset_registration
from bdf_pz.db import register_structured_dataset

preview_query(sql_query, limit=1)

registered_datasets[dataset_name] = {
    "name": dataset_name,
    "source_type": "sql_query",
    "connection_name": connection_name,
    "sql_query": sql_query,
    "catalog_enabled": True,
    "catalog_status": "structured_registered",
    "cached_schemas": [],
}

register_structured_dataset(
    dataset_name=dataset_name,
    connection_name=connection_name,
    source_type="sql_query",
    sql_query=sql_query,
    metadata={"registered_via": "register_query_dataset"},
)
persist_dataset_registration(registered_datasets[dataset_name])

output = f"Registered SQL query dataset '{dataset_name}' for {connection_name}"
print(output)
output
