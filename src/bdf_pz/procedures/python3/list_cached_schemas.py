from prettytable import PrettyTable
from prettytable import TableStyle

from bdf_pz.db import list_cached_schemas

dataset_name = "{{ dataset_name }}".strip() or None

cached_df = list_cached_schemas(dataset_name=dataset_name)

if cached_df.empty:
    if dataset_name:
        output = f"No cached schemas found for dataset '{dataset_name}'."
    else:
        output = "No cached schemas found."
else:
    table = PrettyTable(list(cached_df.columns))
    table.add_rows(cached_df.astype(str).values.tolist())
    table.set_style(TableStyle.MARKDOWN)
    output = table.get_string()

print(output)
output
