import palimpzest as pz

convert_schema = existing_schemas["{{ schema_name }}"]

cardinality_str = "{{cardinality}}"

cardinality = pz.Cardinality.ONE_TO_MANY if cardinality_str == "one_to_many" else pz.Cardinality.ONE_TO_ONE

# assert dataset exists in scope
assert "dataset" in locals(), "Dataset should be defined in the current scope. Please set the input dataset first."

# Pick depends_on candidates by input kind. Image/audio datasets do not
# have a text_contents field, so trying it first produces a confusing
# error that the old code silently swallowed. depends_on=None lets
# Palimpzest pick defaults, which routes images to a vision model.
_input_kind = globals().get("current_input_kind")
if _input_kind in {"image", "audio"}:
    _candidates = (None, ["contents"])
else:
    _candidates = (["text_contents"], ["contents"], None)

_SENTINEL = object()
depends_on = _SENTINEL
_attempts = []
_last_exc = None
for candidate_depends_on in _candidates:
    try:
        if candidate_depends_on is None:
            dataset = dataset.sem_add_columns(convert_schema, cardinality=cardinality)
        else:
            dataset = dataset.sem_add_columns(
                convert_schema,
                cardinality=cardinality,
                depends_on=candidate_depends_on,
            )
        depends_on = candidate_depends_on
        break
    except Exception as exc:
        _attempts.append((candidate_depends_on, f"{type(exc).__name__}: {exc}"))
        _last_exc = exc
        continue

if depends_on is _SENTINEL:
    attempt_summary = "; ".join(
        f"depends_on={cand!r} -> {err}" for cand, err in _attempts
    )
    raise RuntimeError(
        f"convert_dataset failed for schema '{{ schema_name }}' "
        f"(input kind: {_input_kind}). Attempts: {attempt_summary}"
    ) from _last_exc

depends_on_str = "default fields" if depends_on is None else ", ".join(depends_on)
kind_note = f" [input kind: {_input_kind}]" if _input_kind else ""
current_output_schema_name = "{{ schema_name }}"
current_output_cardinality = cardinality_str
output = (
    f"Converted dataset to schema '{{ schema_name }}' "
    f"with cardinality {cardinality_str} using {depends_on_str}{kind_note}"
)
print(output)
output
