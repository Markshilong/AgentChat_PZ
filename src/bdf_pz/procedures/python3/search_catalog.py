from prettytable import PrettyTable
from prettytable import TableStyle

from bdf_pz.db import search_catalog

query = """{{ query }}""".strip()
dataset_name = "{{ dataset_name }}".strip() or None
modality = "{{ modality }}".strip().lower() or None
limit = int({{ limit }})

search_df = search_catalog(
    query=query,
    dataset_name=dataset_name,
    modality=modality,
    limit=limit,
)

if search_df.empty:
    output = f"No catalog matches found for '{query}'."
else:
    table = PrettyTable(list(search_df.columns))
    table.add_rows(search_df.astype(str).values.tolist())
    table.set_style(TableStyle.MARKDOWN)
    output = table.get_string()

print(output)
output
