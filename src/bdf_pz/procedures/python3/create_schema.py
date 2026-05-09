# Define a class name
# Custom __repr__ to output detailed information about the class and its fields
schema = []
schema_name = "{{ schema_name }}"
for idx, field_name in enumerate({{ field_names }}):
    desc = {{ field_descriptions }}[idx]
    field = {"name":field_name, "desc":desc, "type":eval({{ field_types}}[idx])}
    schema.append(field)

existing_schemas[schema_name] = schema
output = f"Created schema '{schema_name}' with fields: {', '.join(field['name'] for field in schema)}"
print(output)
output
