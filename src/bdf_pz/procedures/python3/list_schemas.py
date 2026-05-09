import pandas as pd
from prettytable import PrettyTable

# construct table for printing
table = [["Name", "Fields"]]
for name, value in existing_schemas.items():
    field_names = [field["name"] for field in value]
    table.append([name, ",".join(field_names)])

# print table of registered datasets
t = PrettyTable(table[0])
t.add_rows(table[1:])
output = t.get_string()
print(output)
output
