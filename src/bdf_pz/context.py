# SPDX-FileCopyrightText: 2024-present Brandon Rose <rose.brandon.m@gmail.com>
#
# SPDX-License-Identifier: MIT
from typing import Dict, Any, TYPE_CHECKING
import os

from beaker_kernel.lib import BeakerContext
from beaker_kernel.lib.utils import action

from .agent import BdfPzAgent

if TYPE_CHECKING:
    from beaker_kernel.kernel import BeakerKernel


class BdfPzContext(BeakerContext):
    """
    Biomedical Data Fabric Palimpzest Context Class
    """

    compatible_subkernels = ["python3"]
    SLUG = "bdf-pz"

    def __init__(self, beaker_kernel: "BeakerKernel", config: Dict[str, Any]):
        super().__init__(beaker_kernel, BdfPzAgent, config)

    def _state_preamble(self) -> str:
        return """
import os

if "DATA_PATH" not in globals():
    if os.path.exists("/home/user/testdata"):
        DATA_PATH = "/home/user/testdata"
    elif os.path.exists("testdata"):
        DATA_PATH = "testdata"
    else:
        DATA_PATH = "testdata/"

if "scientific_paper_schema" not in globals():
    scientific_paper_schema = [
        {"name": "paper_title", "type": str, "desc": "The title of the paper. This is a natural language title, not a number or letter."},
        {"name": "author", "type": str, "desc": "The name of the first author of the paper"},
        {"name": "abstract", "type": str, "desc": "A short description of the paper contributions and findings"},
    ]

if "reference_schema" not in globals():
    reference_schema = [
        {"name": "index", "type": int, "desc": "The index of the reference in the paper."},
        {"name": "title", "type": str, "desc": "The title of the paper being cited."},
        {"name": "first_author", "type": str, "desc": "The author of the paper being cited."},
        {"name": "year", "type": int, "desc": "The year in which the cited paper was published."},
    ]

if "image_schema" not in globals():
    image_schema = [
        {"name": "caption", "type": str, "desc": "A one-sentence natural-language caption describing what the image shows."},
        {"name": "description", "type": str, "desc": "A longer paragraph describing the visible contents of the image, including subjects, setting, and notable details."},
        {"name": "objects", "type": str, "desc": "A comma-separated list of distinct objects, people, or entities visible in the image."},
        {"name": "ocr_text", "type": str, "desc": "Any readable text that appears in the image, transcribed verbatim. Empty string if there is no text."},
    ]

if "current_input_kind" not in globals():
    current_input_kind = None

if "registered_datasets" not in globals():
    registered_datasets = {}
    if os.path.exists(DATA_PATH):
        for _name in os.listdir(DATA_PATH):
            registered_datasets[_name] = {
                "name": _name,
                "source_type": "file_path",
                "path": os.path.join(DATA_PATH, _name),
            }
    try:
        from bdf_pz.db import load_persisted_datasets

        registered_datasets.update(load_persisted_datasets())
    except Exception:
        pass

if "existing_schemas" not in globals():
    existing_schemas = {
        "ScientificPaper": scientific_paper_schema,
        "Reference": reference_schema,
        "Image": image_schema,
    }

if "_default_schema_for_kind" not in globals():
    _default_schema_for_kind = {
        "pdf": "ScientificPaper",
        "text": "ScientificPaper",
        "html": "ScientificPaper",
        "image": "Image",
    }

def _normalize_registered_dataset_entry(dataset_name, descriptor):
    if isinstance(descriptor, str):
        return {
            "name": dataset_name,
            "source_type": "file_path",
            "path": descriptor,
        }
    if isinstance(descriptor, dict):
        normalized = dict(descriptor)
        normalized.setdefault("name", dataset_name)
        return normalized
    raise ValueError(f"Unsupported dataset descriptor for {dataset_name}: {type(descriptor)}")

def _get_registered_dataset_entry(dataset_name):
    if dataset_name not in registered_datasets:
        try:
            from bdf_pz.db import load_persisted_datasets

            persisted = load_persisted_datasets()
            registered_datasets.update(persisted)
        except Exception:
            pass
    entry = _normalize_registered_dataset_entry(
        dataset_name,
        registered_datasets[dataset_name],
    )
    registered_datasets[dataset_name] = entry
    return entry

def _dataset_location(entry):
    source_type = entry.get("source_type", "file_path")
    if source_type == "file_path":
        return entry["path"]
    if source_type == "multimodal":
        modalities = entry.get("modalities", {})
        active = [
            f"{kind}:{info.get('count', '?')}"
            for kind, info in modalities.items()
            if info.get("status") != "skipped"
        ]
        return f"{entry.get('path', '')} [{', '.join(active)}]" if active else entry.get("path", "")
    if source_type == "sql_table":
        connection_name = entry.get("connection_name", "local_duckdb")
        table_name = entry["table_name"]
        return f"{connection_name}.{table_name}"
    if source_type == "sql_query":
        connection_name = entry.get("connection_name", "local_duckdb")
        query = entry["sql_query"].strip().replace("\\n", " ")
        if len(query) > 80:
            query = query[:77] + "..."
        return f"{connection_name}: {query}"
    return str(entry)

def _dataset_item_count(entry):
    source_type = entry.get("source_type", "file_path")
    if source_type == "file_path":
        path = entry["path"]
        try:
            return len(os.listdir(path))
        except Exception:
            return 1
    if source_type == "multimodal":
        return sum(
            info.get("count", 0)
            for info in entry.get("modalities", {}).values()
            if info.get("status") != "skipped"
        )
    if source_type == "sql_table":
        connection_name = entry.get("connection_name", "local_duckdb")
        if connection_name not in {"local_duckdb", "duckdb", ""}:
            return "?"
        from bdf_pz.db import preview_query
        table_name = entry["table_name"]
        count_df = preview_query(f'SELECT COUNT(*) AS row_count FROM "{table_name}"', limit=1)
        return int(count_df.iloc[0]["row_count"])
    return "?"

def _dataset_kind_for_class(dataset_cls):
    import palimpzest as pz

    class_kind_map = [
        (pz.PDFFileDataset, "pdf"),
        (pz.TextFileDataset, "text"),
        (pz.HTMLFileDataset, "html"),
        (pz.XLSFileDataset, "xls"),
        (pz.ImageFileDataset, "image"),
        (pz.AudioFileDataset, "audio"),
    ]
    for candidate_cls, kind in class_kind_map:
        if dataset_cls is candidate_cls:
            return kind
    return None

def _partition_files_by_kind(directory_path):
    import os
    import palimpzest as pz

    ext_to_cls = {
        ".txt": pz.TextFileDataset,
        ".text": pz.TextFileDataset,
        ".md": pz.TextFileDataset,
        ".pdf": pz.PDFFileDataset,
        ".html": pz.HTMLFileDataset,
        ".htm": pz.HTMLFileDataset,
        ".xls": pz.XLSFileDataset,
        ".xlsx": pz.XLSFileDataset,
        ".png": pz.ImageFileDataset,
        ".jpg": pz.ImageFileDataset,
        ".jpeg": pz.ImageFileDataset,
        ".gif": pz.ImageFileDataset,
        ".bmp": pz.ImageFileDataset,
        ".webp": pz.ImageFileDataset,
        ".tif": pz.ImageFileDataset,
        ".tiff": pz.ImageFileDataset,
        ".mp3": pz.AudioFileDataset,
        ".wav": pz.AudioFileDataset,
        ".m4a": pz.AudioFileDataset,
        ".flac": pz.AudioFileDataset,
        ".ogg": pz.AudioFileDataset,
    }

    abs_directory = os.path.abspath(directory_path)
    groups = {}
    for entry_name in os.listdir(abs_directory):
        if entry_name.startswith("."):
            continue
        full_path = os.path.join(abs_directory, entry_name)
        if not os.path.isfile(full_path):
            continue
        ext = os.path.splitext(entry_name)[1].lower()
        cls = ext_to_cls.get(ext)
        kind = _dataset_kind_for_class(cls) if cls is not None else "_unknown"
        groups.setdefault(kind, []).append((entry_name, full_path))
    return groups

def _construct_dataset_from_path(dataset_path, dataset_id=None):
    import os
    import palimpzest as pz

    if dataset_id is None:
        dataset_id = os.path.basename(os.path.normpath(dataset_path))

    extension_map = {
        ".txt": pz.TextFileDataset,
        ".text": pz.TextFileDataset,
        ".md": pz.TextFileDataset,
        ".pdf": pz.PDFFileDataset,
        ".html": pz.HTMLFileDataset,
        ".htm": pz.HTMLFileDataset,
        ".xls": pz.XLSFileDataset,
        ".xlsx": pz.XLSFileDataset,
        ".png": pz.ImageFileDataset,
        ".jpg": pz.ImageFileDataset,
        ".jpeg": pz.ImageFileDataset,
        ".gif": pz.ImageFileDataset,
        ".bmp": pz.ImageFileDataset,
        ".webp": pz.ImageFileDataset,
        ".tif": pz.ImageFileDataset,
        ".tiff": pz.ImageFileDataset,
        ".mp3": pz.AudioFileDataset,
        ".wav": pz.AudioFileDataset,
        ".m4a": pz.AudioFileDataset,
        ".flac": pz.AudioFileDataset,
        ".ogg": pz.AudioFileDataset,
    }

    def _dataset_cls_for_extension(ext):
        ext = ext.lower()
        if ext not in extension_map:
            raise ValueError(f"Unsupported dataset file extension: {ext}")
        return extension_map[ext]

    if os.path.isfile(dataset_path):
        dataset_cls = _dataset_cls_for_extension(os.path.splitext(dataset_path)[1])
        return dataset_cls(dataset_id, dataset_path)

    if os.path.isdir(dataset_path):
        entries = [
            name for name in os.listdir(dataset_path)
            if not name.startswith(".") and os.path.isfile(os.path.join(dataset_path, name))
        ]
        if not entries:
            raise ValueError(f"Dataset path has no files: {dataset_path}")

        suffixes = {os.path.splitext(name)[1].lower() for name in entries}
        dataset_classes = {_dataset_cls_for_extension(ext) for ext in suffixes}
        if len(dataset_classes) != 1:
            raise ValueError(
                f"Dataset directory must contain files mapping to one dataset type, "
                f"found extensions: {sorted(suffixes)}"
            )

        dataset_cls = next(iter(dataset_classes))
        return dataset_cls(dataset_id, dataset_path)

    raise ValueError(f"Dataset path is invalid: {dataset_path}")
""".strip()

    def get_code(self, name: str, render_dict: Dict[str, Any] | None = None) -> str:
        code = super().get_code(name, render_dict)
        if name == "setup":
            return code
        return f"{self._state_preamble()}\n\n{code}"
        
    async def setup(self, context_info=None, parent_header=None):
        """
        This runs on setup and invokes the `procedures/python3/setup.py` script to 
        configure the environment appropriately.
        """
        command = "\n".join(
            [
            self.get_code(
                "setup",
                {
                    "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY"),
                    "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY"),
                },
            ),
            ]
        )
        await self.execute(command)

    async def auto_context(self):
            return f"""
            You are an assistant helping biomedical researchers users the Palimpzest library to extract references from scientific papers.
            """.strip()
