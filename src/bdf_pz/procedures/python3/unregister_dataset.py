dataset_name = "{{ dataset_name }}"
from bdf_pz.db import delete_dataset_registration

del registered_datasets[dataset_name]
delete_dataset_registration(dataset_name)
output = f"Unregistered dataset '{dataset_name}'"
print(output)
output
