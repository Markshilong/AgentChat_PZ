import pandas as pd
from prettytable import PrettyTable
from prettytable import TableStyle

from bdf_pz.db import preview_table

result_sink_type = "{{ result_sink_type }}".strip().lower()
result_sink_target = "{{ result_sink_target }}".strip()
limit = int({{ limit }})

if result_sink_type == "duckdb":
    preview_df = preview_table(result_sink_target, limit=limit)
elif result_sink_type == "csv":
    preview_df = pd.read_csv(result_sink_target).head(limit)
else:
    raise ValueError(
        f"Unsupported result_sink_type: {result_sink_type}. "
        "Supported values are 'duckdb' and 'csv'."
    )

if preview_df.empty:
    output = f"No rows found in {result_sink_type} target '{result_sink_target}'."
else:
    table = PrettyTable(list(preview_df.columns))
    table.add_rows(preview_df.astype(str).values.tolist())
    table.set_style(TableStyle.MARKDOWN)
    output = table.get_string()

print(output)
output
