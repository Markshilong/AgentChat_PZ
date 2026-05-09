from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from palimpzest.core.data.iter_dataset import (
    AudioFileDataset,
    HTMLFileDataset,
    ImageFileDataset,
    PDFFileDataset,
    TextFileDataset,
    XLSFileDataset,
)
from palimpzest.policy import MaxQuality, MinCost
from palimpzest.query.processor.config import QueryProcessorConfig

from .db import (
    DOCUMENT_MODALITIES,
    FULL_TEXT_CONTENT_KIND,
    IMAGE_MODALITIES,
    mark_asset_index_status,
    register_dataset_assets,
    replace_asset_content,
    replace_document_sections,
    split_text_into_sections,
    upsert_document_catalog,
    upsert_image_catalog,
)
from .model_selection import get_query_available_models

DOCUMENT_CATALOG_SCHEMA = [
    {
        "name": "paper_title",
        "type": str,
        "desc": "The title of the document or paper in natural language.",
    },
    {
        "name": "abstract",
        "type": str,
        "desc": "A concise abstract or high-level summary of the document.",
    },
    {
        "name": "author",
        "type": str,
        "desc": "The name of the first or primary author when identifiable.",
    },
    {
        "name": "published_date",
        "type": str,
        "desc": "The publication date or year if it is available in the document.",
    },
    {
        "name": "venue",
        "type": str,
        "desc": "The publication venue, journal, or conference if it is available.",
    },
    {
        "name": "topics",
        "type": str,
        "desc": "A comma-separated list of the main keywords or topics discussed in the document.",
    },
    {
        "name": "summary",
        "type": str,
        "desc": "A short summary of the document's main contribution or contents.",
    },
]

IMAGE_CATALOG_SCHEMA = [
    {
        "name": "caption",
        "type": str,
        "desc": "A one-sentence caption describing what the image shows.",
    },
    {
        "name": "description",
        "type": str,
        "desc": "A fuller visual description of the image contents.",
    },
    {
        "name": "objects",
        "type": str,
        "desc": "A comma-separated list of visible entities, objects, or subjects.",
    },
    {
        "name": "ocr_text",
        "type": str,
        "desc": "Any text visible in the image, transcribed verbatim when possible.",
    },
    {
        "name": "topics",
        "type": str,
        "desc": "A comma-separated list of the themes or topics depicted in the image.",
    },
]


def _construct_dataset_from_path(dataset_path: str, dataset_id: str | None = None):
    if dataset_id is None:
        dataset_id = os.path.basename(os.path.normpath(dataset_path))

    extension_map = {
        ".txt": TextFileDataset,
        ".text": TextFileDataset,
        ".md": TextFileDataset,
        ".pdf": PDFFileDataset,
        ".html": HTMLFileDataset,
        ".htm": HTMLFileDataset,
        ".xls": XLSFileDataset,
        ".xlsx": XLSFileDataset,
        ".png": ImageFileDataset,
        ".jpg": ImageFileDataset,
        ".jpeg": ImageFileDataset,
        ".gif": ImageFileDataset,
        ".bmp": ImageFileDataset,
        ".webp": ImageFileDataset,
        ".tif": ImageFileDataset,
        ".tiff": ImageFileDataset,
        ".mp3": AudioFileDataset,
        ".wav": AudioFileDataset,
        ".m4a": AudioFileDataset,
        ".flac": AudioFileDataset,
        ".ogg": AudioFileDataset,
    }

    def _dataset_cls_for_extension(ext: str):
        ext = ext.lower()
        if ext not in extension_map:
            raise ValueError(f"Unsupported dataset file extension: {ext}")
        return extension_map[ext]

    if os.path.isfile(dataset_path):
        dataset_cls = _dataset_cls_for_extension(os.path.splitext(dataset_path)[1])
        return dataset_cls(dataset_id, dataset_path)

    if os.path.isdir(dataset_path):
        entries = [
            name
            for name in os.listdir(dataset_path)
            if not name.startswith(".") and os.path.isfile(os.path.join(dataset_path, name))
        ]
        if not entries:
            raise ValueError(f"Dataset path has no files: {dataset_path}")

        suffixes = {os.path.splitext(name)[1].lower() for name in entries}
        dataset_classes = {_dataset_cls_for_extension(ext) for ext in suffixes}
        if len(dataset_classes) != 1:
            raise ValueError(
                "Dataset directory must contain files mapping to one dataset type, "
                f"found extensions: {sorted(suffixes)}"
            )

        dataset_cls = next(iter(dataset_classes))
        return dataset_cls(dataset_id, dataset_path)

    raise ValueError(f"Dataset path is invalid: {dataset_path}")


def _catalog_query_config() -> QueryProcessorConfig:
    policy_name = os.environ.get("BDF_PZ_CATALOG_POLICY", "max_quality").strip().lower()
    policy = MinCost() if policy_name == "min_cost" else MaxQuality()
    return QueryProcessorConfig(
        policy=policy,
        cache=False,
        verbose=False,
        progress=False,
        available_models=get_query_available_models(),
        allow_code_synth=False,
    )


def _collect_file_paths(dataset_path: str) -> list[str]:
    abs_path = os.path.abspath(dataset_path)
    if os.path.isfile(abs_path):
        return [abs_path]
    if not os.path.isdir(abs_path):
        raise ValueError(f"Dataset path is invalid: {dataset_path}")

    return [
        os.path.join(abs_path, name)
        for name in sorted(os.listdir(abs_path))
        if not name.startswith(".") and os.path.isfile(os.path.join(abs_path, name))
    ]


def _build_temp_dataset(asset_entries: list[dict[str, Any]], prefix: str) -> tuple[str, dict[str, dict[str, Any]]]:
    temp_dir = tempfile.mkdtemp(prefix=f"bdf_pz_catalog_{prefix}_")
    filename_map: dict[str, dict[str, Any]] = {}
    for entry in asset_entries:
        suffix = Path(entry["alias_path"]).suffix.lower()
        temp_name = f"{entry['asset_id']}{suffix}"
        target_path = os.path.join(temp_dir, temp_name)
        if not os.path.exists(target_path):
            os.symlink(entry["source_path"], target_path)
        filename_map[temp_name] = entry
    return temp_dir, filename_map


def _document_depends_on(modality: str) -> tuple[list[str] | None, str]:
    if modality == "pdf":
        return ["text_contents"], "text_contents"
    if modality == "html":
        return ["text"], "text"
    return ["contents"], "contents"


def _first_non_empty_text(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _summarize_text(text: str, max_sentences: int = 3, max_chars: int = 800) -> str | None:
    if not text:
        return None
    normalized = " ".join(str(text).split())
    if not normalized:
        return None
    sentences = re.split(r"(?<=[.!?])\s+", normalized)
    summary = " ".join(sentences[:max_sentences]).strip()
    if not summary:
        summary = normalized[:max_chars].strip()
    if len(summary) > max_chars:
        summary = summary[: max_chars - 3].rstrip() + "..."
    return summary


def _infer_document_catalog_from_text(text_value: Any, filename: str) -> dict[str, str | None]:
    text = "" if text_value is None else str(text_value)
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.strip() for line in normalized.split("\n") if line.strip()]

    title = None
    for line in lines[:12]:
        lowered = line.lower()
        if lowered.startswith("abstract"):
            continue
        if len(line) < 12:
            continue
        if re.fullmatch(r"[\W_]+", line):
            continue
        title = line
        break
    if title is None:
        title = Path(filename).stem.replace("_", " ").strip() or None

    abstract = None
    abstract_match = re.search(
        r"(?is)\babstract\b[:\s-]*(.+?)(?:\n\s*\n|\n[A-Z][A-Z0-9 \-]{2,}\n|\n\d+(\.\d+)*\s+[A-Z].+|\Z)",
        normalized,
    )
    if abstract_match:
        abstract = " ".join(abstract_match.group(1).split())
    elif lines:
        abstract = _summarize_text(" ".join(lines[1:6]), max_sentences=2, max_chars=500)

    published_date = None
    year_match = re.search(r"\b(19|20)\d{2}\b", normalized[:4000])
    if year_match:
        published_date = year_match.group(0)

    summary = _summarize_text(abstract or normalized, max_sentences=3, max_chars=800)

    return {
        "title": title,
        "abstract": abstract,
        "published_date": published_date,
        "summary": summary,
    }


def _index_document_assets(asset_entries: list[dict[str, Any]], modality: str) -> dict[str, Any]:
    asset_ids = [entry["asset_id"] for entry in asset_entries]
    if not asset_ids:
        return {"indexed": 0, "failed": 0, "reused": 0}

    temp_dir, filename_map = _build_temp_dataset(asset_entries, f"{modality}_docs")
    try:
        dataset = _construct_dataset_from_path(temp_dir, f"catalog_{modality}")
        depends_on, content_field = _document_depends_on(modality)
        if depends_on is None:
            mapped = dataset.sem_add_columns(DOCUMENT_CATALOG_SCHEMA)
        else:
            mapped = dataset.sem_add_columns(DOCUMENT_CATALOG_SCHEMA, depends_on=depends_on)
        results_df = mapped.run(_catalog_query_config()).to_df()

        indexed_asset_ids: set[str] = set()
        for _, row in results_df.iterrows():
            filename = os.path.basename(str(row.get("filename", "")).strip())
            asset_entry = filename_map.get(filename)
            if asset_entry is None:
                continue
            asset_id = asset_entry["asset_id"]
            indexed_asset_ids.add(asset_id)
            text_value = row.get(content_field)
            if text_value is None:
                text_value = row.get("text_contents")
            if text_value is None:
                text_value = row.get("contents")
            if text_value is None:
                text_value = row.get("text")

            inferred = _infer_document_catalog_from_text(text_value, filename)
            title = _first_non_empty_text(row.get("paper_title"), row.get("title"), inferred["title"])
            abstract = _first_non_empty_text(row.get("abstract"), inferred["abstract"])
            authors = _first_non_empty_text(row.get("authors"), row.get("author"))
            published_date = _first_non_empty_text(row.get("published_date"), inferred["published_date"])
            venue = _first_non_empty_text(row.get("venue"))
            topics = _first_non_empty_text(row.get("topics"))
            summary = _first_non_empty_text(row.get("summary"), abstract, inferred["summary"])

            upsert_document_catalog(
                asset_id=asset_id,
                title=title,
                abstract=abstract,
                authors=authors,
                published_date=published_date,
                venue=venue,
                topics=topics,
                summary=summary,
            )
            replace_asset_content(asset_id, FULL_TEXT_CONTENT_KIND, text_value)
            replace_document_sections(asset_id, split_text_into_sections(text_value))

        failed_asset_ids = sorted(set(asset_ids) - indexed_asset_ids)
        if indexed_asset_ids:
            mark_asset_index_status(indexed_asset_ids, "indexed")
        if failed_asset_ids:
            mark_asset_index_status(
                failed_asset_ids,
                "error",
                error_message="Catalog extraction did not yield rows for one or more assets.",
            )
        return {
            "indexed": len(indexed_asset_ids),
            "failed": len(failed_asset_ids),
            "reused": 0,
        }
    except Exception as exc:
        mark_asset_index_status(asset_ids, "error", error_message=f"{type(exc).__name__}: {exc}")
        return {
            "indexed": 0,
            "failed": len(asset_ids),
            "reused": 0,
            "error": f"{type(exc).__name__}: {exc}",
        }
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _index_image_assets(asset_entries: list[dict[str, Any]]) -> dict[str, Any]:
    asset_ids = [entry["asset_id"] for entry in asset_entries]
    if not asset_ids:
        return {"indexed": 0, "failed": 0, "reused": 0}

    temp_dir, filename_map = _build_temp_dataset(asset_entries, "images")
    try:
        dataset = _construct_dataset_from_path(temp_dir, "catalog_images")
        results_df = dataset.sem_add_columns(IMAGE_CATALOG_SCHEMA).run(_catalog_query_config()).to_df()

        indexed_asset_ids: set[str] = set()
        for _, row in results_df.iterrows():
            filename = os.path.basename(str(row.get("filename", "")).strip())
            asset_entry = filename_map.get(filename)
            if asset_entry is None:
                continue
            asset_id = asset_entry["asset_id"]
            indexed_asset_ids.add(asset_id)
            upsert_image_catalog(
                asset_id=asset_id,
                caption=row.get("caption"),
                description=row.get("description"),
                objects=row.get("objects"),
                ocr_text=row.get("ocr_text"),
                topics=row.get("topics"),
            )

        failed_asset_ids = sorted(set(asset_ids) - indexed_asset_ids)
        if indexed_asset_ids:
            mark_asset_index_status(indexed_asset_ids, "indexed")
        if failed_asset_ids:
            mark_asset_index_status(
                failed_asset_ids,
                "error",
                error_message="Image catalog extraction did not yield rows for one or more assets.",
            )
        return {
            "indexed": len(indexed_asset_ids),
            "failed": len(failed_asset_ids),
            "reused": 0,
        }
    except Exception as exc:
        mark_asset_index_status(asset_ids, "error", error_message=f"{type(exc).__name__}: {exc}")
        return {
            "indexed": 0,
            "failed": len(asset_ids),
            "reused": 0,
            "error": f"{type(exc).__name__}: {exc}",
        }
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def build_catalog_for_paths(
    dataset_name: str,
    asset_paths: list[str],
    source_type: str = "file_path",
) -> dict[str, Any]:
    """
    Build or reuse catalog rows for a set of assets and return a compact summary.

    Registration never fails purely because catalog extraction fails. The summary is
    designed to be surfaced back to the user while preserving the file-backed workflow.
    """
    descriptors = register_dataset_assets(dataset_name, asset_paths, source_type=source_type)
    by_modality: dict[str, list[dict[str, Any]]] = {}
    for descriptor in descriptors:
        by_modality.setdefault(descriptor["modality"], []).append(descriptor)

    summary = {
        "dataset_name": dataset_name,
        "total_assets": len(descriptors),
        "indexed_assets": 0,
        "reused_assets": 0,
        "failed_assets": 0,
        "skipped_assets": 0,
        "modalities": {},
        "errors": [],
    }

    for modality, entries in sorted(by_modality.items()):
        pending_entries = [entry for entry in entries if entry["needs_index"]]
        reused_count = len(entries) - len(pending_entries)

        if modality in DOCUMENT_MODALITIES:
            result = _index_document_assets(pending_entries, modality)
        elif modality in IMAGE_MODALITIES:
            result = _index_image_assets(pending_entries)
        else:
            result = {"indexed": 0, "failed": 0, "reused": 0}
            summary["skipped_assets"] += len(entries)

        summary["indexed_assets"] += result.get("indexed", 0)
        summary["failed_assets"] += result.get("failed", 0)
        summary["reused_assets"] += reused_count
        if result.get("error"):
            summary["errors"].append(f"{modality}: {result['error']}")
        summary["modalities"][modality] = {
            "total": len(entries),
            "indexed": result.get("indexed", 0),
            "reused": reused_count,
            "failed": result.get("failed", 0),
        }

    return summary


def build_catalog_for_dataset(
    dataset_name: str,
    dataset_path: str,
    source_type: str = "file_path",
) -> dict[str, Any]:
    """Build catalog rows for a file-backed dataset path."""
    asset_paths = _collect_file_paths(dataset_path)
    return build_catalog_for_paths(dataset_name, asset_paths, source_type=source_type)
