from prettytable import PrettyTable
from prettytable import TableStyle

from bdf_pz.db import preview_cached_schema

dataset_name = "{{ dataset_name }}".strip()
schema_name = "{{ schema_name }}".strip()
limit = int({{ limit }})

preview_df = preview_cached_schema(
    dataset_name=dataset_name,
    schema_name=schema_name,
    limit=limit,
)

if preview_df.empty:
    output = (
        f"No cached rows found for schema '{schema_name}' in dataset "
        f"'{dataset_name}'."
    )
else:
    table = PrettyTable(list(preview_df.columns))
    table.add_rows(preview_df.astype(str).values.tolist())
    table.set_style(TableStyle.MARKDOWN)
    output = table.get_string()

print(output)
output
