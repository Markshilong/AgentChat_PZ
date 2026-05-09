import pandas as pd
from prettytable import PrettyTable
from prettytable import TableStyle

# construct table for printing
table = [["Name", "Source Type", "Location", "N. Items", "Catalog", "Cached Schemas"]]
for dataset_name in sorted(registered_datasets):
    entry = _get_registered_dataset_entry(dataset_name)
    source_type = entry.get("source_type", "file_path")
    location = _dataset_location(entry)
    n_items = _dataset_item_count(entry)
    catalog_status = entry.get("catalog_status", "-")
    cached_schemas = ", ".join(entry.get("cached_schemas", [])) or "-"
    table.append([
        dataset_name,
        source_type,
        location,
        n_items,
        catalog_status,
        cached_schemas,
    ])

# print table of registered datasets
t = PrettyTable(table[0])
t.add_rows(table[1:])
t.set_style(TableStyle.MARKDOWN)
output = t.get_string()
print(output)
output
