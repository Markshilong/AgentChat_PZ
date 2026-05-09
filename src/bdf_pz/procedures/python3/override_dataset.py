try:
    del dataset
except NameError:
    pass
dataset_name = "{{ dataset_name }}"
entry = _get_registered_dataset_entry(dataset_name)
source_type = entry.get("source_type", "file_path")

if source_type != "file_path":
    raise ValueError(
        f"Dataset '{dataset_name}' has source_type '{source_type}'. "
        "Structured datasets can be registered and previewed in the MVP, "
        "but they must be materialized into a file-backed Palimpzest input before "
        "overriding the working dataset."
    )

dataset_path = entry["path"]
dataset = _construct_dataset_from_path(dataset_path, "{{ dataset_name }}")
current_input_dataset_name = dataset_name
current_input_source_type = source_type
current_input_kind = _dataset_kind_for_class(type(dataset))

output = f"Overrode working dataset with '{{ dataset_name }}' from {dataset_path}"
print(output)
output
