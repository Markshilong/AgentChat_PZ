# SPDX-FileCopyrightText: 2024-present Brandon Rose <rose.brandon.m@gmail.com>
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

DEFAULT_DUCKDB_PATH = "/tmp/bdf_pz.duckdb"
DEFAULT_PREVIEW_LIMIT = 50
FULL_TEXT_CONTENT_KIND = "full_text"

DOCUMENT_MODALITIES = {"pdf", "text", "html"}
IMAGE_MODALITIES = {"image"}
STRUCTURED_FILE_EXTENSIONS = {".csv", ".tsv"}

_TEXT_EXTENSIONS = {".txt", ".text", ".md"}
_PDF_EXTENSIONS = {".pdf"}
_HTML_EXTENSIONS = {".html", ".htm"}
_IMAGE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".bmp",
    ".webp",
    ".tif",
    ".tiff",
}
_XLS_EXTENSIONS = {".xls", ".xlsx"}
_AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac", ".ogg"}


def get_duckdb_path() -> str:
    """Return the configured DuckDB path, creating parent dirs if needed."""
    configured_path = os.environ.get("DUCKDB_PATH")
    if configured_path:
        db_path = Path(configured_path).expanduser()
    else:
        local_default = Path(".data") / "bdf_pz.duckdb"
        db_path = local_default if local_default.exists() else Path(DEFAULT_DUCKDB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return str(db_path)


def get_duckdb_connection(read_only: bool = False):
    """Open a DuckDB connection using the configured local database path."""
    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError(
            "duckdb is not installed. Add it to the project dependencies before "
            "using database-backed result sinks or structured datasets."
        ) from exc

    return duckdb.connect(get_duckdb_path(), read_only=read_only)


def normalize_identifier(name: str) -> str:
    """Convert user-facing names into safe SQL identifiers."""
    identifier = re.sub(r"[^0-9a-zA-Z_]+", "_", name.strip()).strip("_").lower()
    if not identifier:
        raise ValueError("Identifier must contain at least one alphanumeric character.")
    if identifier[0].isdigit():
        identifier = f"t_{identifier}"
    return identifier


def ensure_catalog_tables() -> None:
    """Create the local catalog/cache tables if they do not already exist."""
    conn = get_duckdb_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS assets (
                asset_id VARCHAR PRIMARY KEY,
                modality VARCHAR NOT NULL,
                content_hash VARCHAR NOT NULL,
                source_path VARCHAR NOT NULL,
                size_bytes BIGINT NOT NULL,
                mtime DOUBLE NOT NULL,
                created_at TIMESTAMP NOT NULL,
                last_indexed_at TIMESTAMP,
                index_status VARCHAR NOT NULL,
                last_error VARCHAR
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS dataset_assets (
                dataset_name VARCHAR NOT NULL,
                asset_id VARCHAR NOT NULL,
                alias_path VARCHAR NOT NULL,
                source_type VARCHAR NOT NULL,
                added_at TIMESTAMP NOT NULL,
                PRIMARY KEY (dataset_name, asset_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS document_catalog (
                asset_id VARCHAR PRIMARY KEY,
                title VARCHAR,
                abstract VARCHAR,
                authors_json VARCHAR,
                published_date VARCHAR,
                venue VARCHAR,
                topics_json VARCHAR,
                summary VARCHAR,
                updated_at TIMESTAMP NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS asset_contents (
                asset_id VARCHAR NOT NULL,
                content_kind VARCHAR NOT NULL,
                text_content VARCHAR,
                updated_at TIMESTAMP NOT NULL,
                PRIMARY KEY (asset_id, content_kind)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS document_sections (
                asset_id VARCHAR NOT NULL,
                section_order INTEGER NOT NULL,
                section_name VARCHAR NOT NULL,
                section_text VARCHAR,
                updated_at TIMESTAMP NOT NULL,
                PRIMARY KEY (asset_id, section_order)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS image_catalog (
                asset_id VARCHAR PRIMARY KEY,
                caption VARCHAR,
                description VARCHAR,
                objects_json VARCHAR,
                ocr_text VARCHAR,
                topics_json VARCHAR,
                updated_at TIMESTAMP NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS structured_datasets (
                dataset_name VARCHAR PRIMARY KEY,
                connection_name VARCHAR NOT NULL,
                source_type VARCHAR NOT NULL,
                target_name VARCHAR,
                sql_query VARCHAR,
                metadata_json VARCHAR,
                updated_at TIMESTAMP NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS dataset_registry (
                dataset_name VARCHAR PRIMARY KEY,
                source_type VARCHAR NOT NULL,
                path VARCHAR,
                connection_name VARCHAR,
                table_name VARCHAR,
                sql_query VARCHAR,
                metadata_json VARCHAR,
                updated_at TIMESTAMP NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS derived_extractions (
                dataset_name VARCHAR NOT NULL,
                asset_id VARCHAR NOT NULL,
                schema_name VARCHAR NOT NULL,
                schema_version VARCHAR NOT NULL,
                row_index INTEGER NOT NULL,
                source_output_dataset VARCHAR,
                result_json VARCHAR NOT NULL,
                result_columns_json VARCHAR NOT NULL,
                updated_at TIMESTAMP NOT NULL,
                PRIMARY KEY (dataset_name, asset_id, schema_name, schema_version, row_index)
            )
            """
        )
    finally:
        conn.close()


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True)


def _normalize_json_value(value: Any) -> Any:
    if pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_normalize_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _normalize_json_value(v) for k, v in value.items()}
    return value


def _listify_text_value(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        items = value
    else:
        text = str(value).strip()
        if not text:
            return []
        normalized = text.replace("\n", ",").replace(";", ",")
        items = normalized.split(",")
    cleaned = []
    for item in items:
        text = str(item).strip()
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned


def guess_modality_from_path(path: str) -> str:
    """Infer the dataset modality from the file extension."""
    ext = Path(path).suffix.lower()
    if ext in _PDF_EXTENSIONS:
        return "pdf"
    if ext in _TEXT_EXTENSIONS:
        return "text"
    if ext in _HTML_EXTENSIONS:
        return "html"
    if ext in _IMAGE_EXTENSIONS:
        return "image"
    if ext in STRUCTURED_FILE_EXTENSIONS:
        return "structured"
    if ext in _XLS_EXTENSIONS:
        return "xls"
    if ext in _AUDIO_EXTENSIONS:
        return "audio"
    return "unknown"


def split_text_into_sections(text: str) -> list[dict[str, Any]]:
    """
    Split a document into coarse sections using lightweight heading heuristics.

    This is intentionally simple: the goal is to persist section-sized context for
    later reuse without re-sending the whole document.
    """
    if text is None:
        return []

    normalized = str(text).replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []

    heading_patterns = [
        re.compile(r"^\d+(\.\d+)*\s+[A-Z][A-Za-z0-9 ,:/()\-]{2,}$"),
        re.compile(r"^(abstract|introduction|background|methods?|materials?|results?|discussion|conclusion|references)\s*$", re.IGNORECASE),
        re.compile(r"^[A-Z][A-Z0-9 /,\-]{3,}$"),
    ]

    def is_heading(line: str) -> bool:
        stripped = line.strip()
        if not stripped:
            return False
        if len(stripped) > 120:
            return False
        return any(pattern.match(stripped) for pattern in heading_patterns)

    sections: list[dict[str, Any]] = []
    current_name = "document_start"
    current_lines: list[str] = []

    for raw_line in normalized.split("\n"):
        line = raw_line.strip()
        if not line:
            if current_lines and current_lines[-1] != "":
                current_lines.append("")
            continue
        if is_heading(line) and current_lines:
            section_text = "\n".join(current_lines).strip()
            if section_text:
                sections.append(
                    {
                        "section_name": current_name,
                        "section_text": section_text,
                    }
                )
            current_name = line
            current_lines = []
            continue
        current_lines.append(line)

    final_text = "\n".join(current_lines).strip()
    if final_text:
        sections.append({"section_name": current_name, "section_text": final_text})

    if not sections:
        sections.append({"section_name": "document_start", "section_text": normalized})

    normalized_sections = []
    for idx, section in enumerate(sections):
        normalized_sections.append(
            {
                "section_order": idx,
                "section_name": section["section_name"] or f"section_{idx}",
                "section_text": section["section_text"],
            }
        )
    return normalized_sections


def compute_file_fingerprint(path: str) -> dict[str, Any]:
    """Compute stable file metadata used for asset registration and reuse."""
    resolved_path = os.path.realpath(os.path.abspath(path))
    modality = guess_modality_from_path(resolved_path)
    sha = hashlib.sha256()
    with open(resolved_path, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            sha.update(chunk)
    content_hash = sha.hexdigest()
    asset_id = hashlib.sha256(f"{modality}\0{content_hash}".encode("utf-8")).hexdigest()
    stat = os.stat(resolved_path)
    return {
        "asset_id": asset_id,
        "content_hash": content_hash,
        "modality": modality,
        "source_path": resolved_path,
        "size_bytes": int(stat.st_size),
        "mtime": float(stat.st_mtime),
    }


def _asset_has_catalog(conn, asset_id: str, modality: str) -> bool:
    if modality in DOCUMENT_MODALITIES:
        populated_catalog_count = conn.execute(
            "SELECT COUNT(*) FROM document_catalog WHERE asset_id = ?",
            [asset_id],
        ).fetchone()[0]
        populated_metadata_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM document_catalog
            WHERE asset_id = ?
              AND (
                NULLIF(trim(COALESCE(title, '')), '') IS NOT NULL
                OR NULLIF(trim(COALESCE(abstract, '')), '') IS NOT NULL
                OR NULLIF(trim(COALESCE(summary, '')), '') IS NOT NULL
                OR NULLIF(trim(COALESCE(authors_json, '[]')), '[]') IS NOT NULL
                OR NULLIF(trim(COALESCE(topics_json, '[]')), '[]') IS NOT NULL
                OR NULLIF(trim(COALESCE(published_date, '')), '') IS NOT NULL
                OR NULLIF(trim(COALESCE(venue, '')), '') IS NOT NULL
              )
            """,
            [asset_id],
        ).fetchone()[0]
        content_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM asset_contents
            WHERE asset_id = ? AND content_kind = ?
            """,
            [asset_id, FULL_TEXT_CONTENT_KIND],
        ).fetchone()[0]
        return bool(populated_catalog_count and populated_metadata_count and content_count)
    if modality in IMAGE_MODALITIES:
        catalog_count = conn.execute(
            "SELECT COUNT(*) FROM image_catalog WHERE asset_id = ?",
            [asset_id],
        ).fetchone()[0]
        populated_metadata_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM image_catalog
            WHERE asset_id = ?
              AND (
                NULLIF(trim(COALESCE(caption, '')), '') IS NOT NULL
                OR NULLIF(trim(COALESCE(description, '')), '') IS NOT NULL
                OR NULLIF(trim(COALESCE(ocr_text, '')), '') IS NOT NULL
                OR NULLIF(trim(COALESCE(objects_json, '[]')), '[]') IS NOT NULL
                OR NULLIF(trim(COALESCE(topics_json, '[]')), '[]') IS NOT NULL
              )
            """,
            [asset_id],
        ).fetchone()[0]
        return bool(catalog_count and populated_metadata_count)
    return True


def register_dataset_assets(
    dataset_name: str,
    asset_paths: Iterable[str],
    source_type: str = "file_path",
) -> list[dict[str, Any]]:
    """
    Register file assets for a dataset and return per-asset indexing metadata.

    Assets are keyed by content hash so repeated registrations across datasets can
    reuse the same catalog/cache rows.
    """
    ensure_catalog_tables()
    now = _utcnow()
    normalized_dataset_name = dataset_name.strip()
    descriptors: list[dict[str, Any]] = []
    conn = get_duckdb_connection()
    try:
        seen_asset_ids: set[str] = set()
        for raw_path in asset_paths:
            if raw_path is None:
                continue
            alias_path = os.path.abspath(os.path.expanduser(str(raw_path).strip()))
            if not alias_path or not os.path.exists(alias_path) or not os.path.isfile(alias_path):
                continue

            fingerprint = compute_file_fingerprint(alias_path)
            asset_id = fingerprint["asset_id"]
            modality = fingerprint["modality"]
            if modality in {"structured", "unknown"}:
                continue
            if asset_id in seen_asset_ids:
                conn.execute(
                    """
                    DELETE FROM dataset_assets
                    WHERE dataset_name = ? AND alias_path = ?
                    """,
                    [normalized_dataset_name, alias_path],
                )
                continue
            seen_asset_ids.add(asset_id)

            existing = conn.execute(
                """
                SELECT created_at, index_status
                FROM assets
                WHERE asset_id = ?
                """,
                [asset_id],
            ).fetchone()

            conn.execute("DELETE FROM assets WHERE asset_id = ?", [asset_id])
            conn.execute(
                """
                INSERT INTO assets (
                    asset_id,
                    modality,
                    content_hash,
                    source_path,
                    size_bytes,
                    mtime,
                    created_at,
                    last_indexed_at,
                    index_status,
                    last_error
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    asset_id,
                    modality,
                    fingerprint["content_hash"],
                    fingerprint["source_path"],
                    fingerprint["size_bytes"],
                    fingerprint["mtime"],
                    existing[0] if existing else now,
                    None,
                    existing[1] if existing else "pending",
                    None,
                ],
            )

            conn.execute(
                """
                DELETE FROM dataset_assets
                WHERE dataset_name = ? AND alias_path = ?
                """,
                [normalized_dataset_name, alias_path],
            )
            conn.execute(
                """
                DELETE FROM dataset_assets
                WHERE dataset_name = ? AND asset_id = ?
                """,
                [normalized_dataset_name, asset_id],
            )
            conn.execute(
                """
                INSERT INTO dataset_assets (
                    dataset_name,
                    asset_id,
                    alias_path,
                    source_type,
                    added_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                [normalized_dataset_name, asset_id, alias_path, source_type, now],
            )

            asset_indexed = _asset_has_catalog(conn, asset_id, modality)
            if asset_indexed:
                conn.execute(
                    """
                    UPDATE assets
                    SET index_status = ?, last_error = NULL
                    WHERE asset_id = ?
                    """,
                    ["indexed", asset_id],
                )
            descriptors.append(
                {
                    **fingerprint,
                    "alias_path": alias_path,
                    "dataset_name": normalized_dataset_name,
                    "needs_index": not asset_indexed,
                }
            )
    finally:
        conn.close()

    return descriptors


def mark_asset_index_status(
    asset_ids: Iterable[str],
    status: str,
    error_message: str | None = None,
) -> None:
    """Update index status for one or more assets."""
    asset_ids = [asset_id for asset_id in asset_ids if asset_id]
    if not asset_ids:
        return

    ensure_catalog_tables()
    conn = get_duckdb_connection()
    try:
        for asset_id in asset_ids:
            conn.execute(
                """
                UPDATE assets
                SET index_status = ?, last_indexed_at = ?, last_error = ?
                WHERE asset_id = ?
                """,
                [status, _utcnow(), error_message, asset_id],
            )
    finally:
        conn.close()


def upsert_document_catalog(
    asset_id: str,
    title: Any = None,
    abstract: Any = None,
    authors: Any = None,
    published_date: Any = None,
    venue: Any = None,
    topics: Any = None,
    summary: Any = None,
) -> None:
    """Persist a normalized document catalog row."""
    ensure_catalog_tables()
    now = _utcnow()
    conn = get_duckdb_connection()
    try:
        conn.execute("DELETE FROM document_catalog WHERE asset_id = ?", [asset_id])
        conn.execute(
            """
            INSERT INTO document_catalog (
                asset_id,
                title,
                abstract,
                authors_json,
                published_date,
                venue,
                topics_json,
                summary,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                asset_id,
                None if title is None else str(title).strip(),
                None if abstract is None else str(abstract).strip(),
                _json_dumps(_listify_text_value(authors)),
                None if published_date is None else str(published_date).strip(),
                None if venue is None else str(venue).strip(),
                _json_dumps(_listify_text_value(topics)),
                None if summary is None else str(summary).strip(),
                now,
            ],
        )
    finally:
        conn.close()


def replace_asset_content(
    asset_id: str,
    content_kind: str,
    text_content: Any,
) -> None:
    """Replace the cached text content for a single asset/content kind."""
    ensure_catalog_tables()
    conn = get_duckdb_connection()
    try:
        conn.execute(
            """
            DELETE FROM asset_contents
            WHERE asset_id = ? AND content_kind = ?
            """,
            [asset_id, content_kind],
        )
        conn.execute(
            """
            INSERT INTO asset_contents (
                asset_id,
                content_kind,
                text_content,
                updated_at
            )
            VALUES (?, ?, ?, ?)
            """,
            [asset_id, content_kind, None if text_content is None else str(text_content), _utcnow()],
        )
    finally:
        conn.close()


def replace_document_sections(
    asset_id: str,
    sections: Iterable[dict[str, Any]],
) -> None:
    """Replace sectionized text for a document asset."""
    ensure_catalog_tables()
    conn = get_duckdb_connection()
    try:
        conn.execute("DELETE FROM document_sections WHERE asset_id = ?", [asset_id])
        for index, section in enumerate(sections):
            conn.execute(
                """
                INSERT INTO document_sections (
                    asset_id,
                    section_order,
                    section_name,
                    section_text,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    asset_id,
                    int(section.get("section_order", index)),
                    str(section.get("section_name", f"section_{index}")),
                    None if section.get("section_text") is None else str(section["section_text"]),
                    _utcnow(),
                ],
            )
    finally:
        conn.close()


def upsert_image_catalog(
    asset_id: str,
    caption: Any = None,
    description: Any = None,
    objects: Any = None,
    ocr_text: Any = None,
    topics: Any = None,
) -> None:
    """Persist a normalized image catalog row."""
    ensure_catalog_tables()
    now = _utcnow()
    conn = get_duckdb_connection()
    try:
        conn.execute("DELETE FROM image_catalog WHERE asset_id = ?", [asset_id])
        conn.execute(
            """
            INSERT INTO image_catalog (
                asset_id,
                caption,
                description,
                objects_json,
                ocr_text,
                topics_json,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                asset_id,
                None if caption is None else str(caption).strip(),
                None if description is None else str(description).strip(),
                _json_dumps(_listify_text_value(objects)),
                None if ocr_text is None else str(ocr_text).strip(),
                _json_dumps(_listify_text_value(topics)),
                now,
            ],
        )
    finally:
        conn.close()


def register_structured_dataset(
    dataset_name: str,
    connection_name: str,
    source_type: str,
    target_name: str | None = None,
    sql_query: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Persist metadata for a structured dataset registration."""
    ensure_catalog_tables()
    conn = get_duckdb_connection()
    try:
        conn.execute(
            "DELETE FROM structured_datasets WHERE dataset_name = ?",
            [dataset_name],
        )
        conn.execute(
            """
            INSERT INTO structured_datasets (
                dataset_name,
                connection_name,
                source_type,
                target_name,
                sql_query,
                metadata_json,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                dataset_name,
                connection_name,
                source_type,
                target_name,
                sql_query,
                _json_dumps(metadata or {}),
                _utcnow(),
            ],
        )
    finally:
        conn.close()


def persist_dataset_registration(entry: dict[str, Any]) -> None:
    """Persist a registered dataset entry so it survives kernel restarts."""
    ensure_catalog_tables()
    normalized = dict(entry)
    dataset_name = normalized["name"]
    conn = get_duckdb_connection()
    try:
        conn.execute("DELETE FROM dataset_registry WHERE dataset_name = ?", [dataset_name])
        conn.execute(
            """
            INSERT INTO dataset_registry (
                dataset_name,
                source_type,
                path,
                connection_name,
                table_name,
                sql_query,
                metadata_json,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                dataset_name,
                normalized.get("source_type", "file_path"),
                normalized.get("path"),
                normalized.get("connection_name"),
                normalized.get("table_name"),
                normalized.get("sql_query"),
                _json_dumps(normalized),
                _utcnow(),
            ],
        )
    finally:
        conn.close()


def delete_dataset_registration(dataset_name: str) -> None:
    """Delete a persisted dataset registration."""
    ensure_catalog_tables()
    conn = get_duckdb_connection()
    try:
        conn.execute("DELETE FROM dataset_registry WHERE dataset_name = ?", [dataset_name])
        conn.execute("DELETE FROM structured_datasets WHERE dataset_name = ?", [dataset_name])
    finally:
        conn.close()


def _infer_dataset_entry_from_assets(
    dataset_name: str,
    rows: list[tuple[str, str]],
) -> dict[str, Any] | None:
    if not rows:
        return None

    source_types = {source_type for source_type, _ in rows if source_type}
    source_type = next(iter(source_types)) if len(source_types) == 1 else "file_path"
    alias_paths = [alias_path for _, alias_path in rows if alias_path]
    if not alias_paths:
        return None

    common_path = os.path.commonpath(alias_paths)
    inferred_path = alias_paths[0] if len(alias_paths) == 1 and os.path.isfile(alias_paths[0]) else common_path
    entry: dict[str, Any] = {
        "name": dataset_name,
        "source_type": source_type,
        "path": inferred_path,
        "catalog_enabled": True,
        "cached_schemas": [],
    }
    return entry


def load_persisted_datasets() -> dict[str, dict[str, Any]]:
    """
    Load persisted dataset registrations and best-effort fallbacks from DuckDB.

    This lets dataset names survive kernel restarts even though the live workflow
    objects still remain kernel-scoped.
    """
    ensure_catalog_tables()
    conn = get_duckdb_connection(read_only=True)
    try:
        registry_rows = conn.execute(
            """
            SELECT dataset_name, metadata_json
            FROM dataset_registry
            ORDER BY dataset_name
            """
        ).fetchall()
        asset_rows = conn.execute(
            """
            SELECT dataset_name, source_type, alias_path
            FROM dataset_assets
            ORDER BY dataset_name, alias_path
            """
        ).fetchall()
    finally:
        conn.close()

    datasets: dict[str, dict[str, Any]] = {}
    for dataset_name, metadata_json in registry_rows:
        try:
            entry = json.loads(metadata_json) if metadata_json else {}
        except Exception:
            entry = {}
        if not isinstance(entry, dict):
            entry = {}
        entry.setdefault("name", dataset_name)
        datasets[dataset_name] = entry

    grouped_asset_rows: dict[str, list[tuple[str, str]]] = {}
    for dataset_name, source_type, alias_path in asset_rows:
        grouped_asset_rows.setdefault(dataset_name, []).append((source_type, alias_path))

    for dataset_name, rows in grouped_asset_rows.items():
        if dataset_name in datasets:
            continue
        inferred = _infer_dataset_entry_from_assets(dataset_name, rows)
        if inferred is not None:
            datasets[dataset_name] = inferred

    for dataset_name, entry in datasets.items():
        entry.setdefault("name", dataset_name)
        entry.setdefault("catalog_enabled", True)
        entry.setdefault("cached_schemas", [])
        try:
            summary = summarize_dataset_catalog(dataset_name)
            if summary["indexed_asset_count"] > 0 and summary["pending_asset_count"] == 0:
                entry.setdefault("catalog_status", "ready")
            elif summary["error_asset_count"] > 0:
                entry.setdefault("catalog_status", "partial")
            elif summary["pending_asset_count"] > 0:
                entry.setdefault("catalog_status", "pending")
            elif summary["asset_count"] > 0:
                entry.setdefault("catalog_status", "registered_only")
            entry.setdefault("catalog_summary", {"persisted": summary})
        except Exception:
            pass

    return datasets


def import_delimited_file(
    path: str,
    table_name: str,
    if_exists: str = "replace",
) -> str:
    """Import a CSV/TSV file into DuckDB and return the normalized table name."""
    if if_exists not in {"replace", "fail"}:
        raise ValueError("if_exists must be one of: replace, fail")

    normalized_table = normalize_identifier(table_name)
    abs_path = os.path.abspath(os.path.expanduser(path))
    delimiter = "\t" if abs_path.lower().endswith(".tsv") else ","

    conn = get_duckdb_connection()
    try:
        table_exists = (
            conn.execute(
                """
                SELECT COUNT(*)
                FROM information_schema.tables
                WHERE table_schema = 'main' AND table_name = ?
                """,
                [normalized_table],
            ).fetchone()[0]
            > 0
        )
        if table_exists and if_exists == "fail":
            raise ValueError(f"Table already exists: {normalized_table}")
        if table_exists and if_exists == "replace":
            conn.execute(f'DROP TABLE IF EXISTS "{normalized_table}"')
        conn.execute(
            f"""
            CREATE TABLE "{normalized_table}" AS
            SELECT * FROM read_csv_auto(?, delim=?)
            """,
            [abs_path, delimiter],
        )
    finally:
        conn.close()

    return normalized_table


def write_dataframe(
    df: pd.DataFrame,
    table_name: str,
    if_exists: str = "replace",
) -> str:
    """
    Persist a dataframe to DuckDB and return the normalized table name.

    Supported modes:
    - replace
    - append
    - fail
    """
    if if_exists not in {"replace", "append", "fail"}:
        raise ValueError("if_exists must be one of: replace, append, fail")

    normalized_table = normalize_identifier(table_name)
    conn = get_duckdb_connection()
    try:
        table_exists = (
            conn.execute(
                """
                SELECT COUNT(*)
                FROM information_schema.tables
                WHERE table_schema = 'main' AND table_name = ?
                """,
                [normalized_table],
            ).fetchone()[0]
            > 0
        )

        if table_exists and if_exists == "fail":
            raise ValueError(f"Table already exists: {normalized_table}")

        conn.register("_bdf_pz_df", df)
        if if_exists == "replace":
            conn.execute(f'DROP TABLE IF EXISTS "{normalized_table}"')
            conn.execute(
                f'CREATE TABLE "{normalized_table}" AS SELECT * FROM _bdf_pz_df'
            )
        elif if_exists == "append":
            if table_exists:
                conn.execute(
                    f'INSERT INTO "{normalized_table}" SELECT * FROM _bdf_pz_df'
                )
            else:
                conn.execute(
                    f'CREATE TABLE "{normalized_table}" AS SELECT * FROM _bdf_pz_df'
                )
        else:
            conn.execute(
                f'CREATE TABLE "{normalized_table}" AS SELECT * FROM _bdf_pz_df'
            )
    finally:
        conn.close()

    return normalized_table


def preview_table(table_name: str, limit: int = DEFAULT_PREVIEW_LIMIT) -> pd.DataFrame:
    """Return the first `limit` rows from a DuckDB table."""
    normalized_table = normalize_identifier(table_name)
    conn = get_duckdb_connection(read_only=True)
    try:
        return conn.execute(
            f'SELECT * FROM "{normalized_table}" LIMIT ?',
            [max(0, int(limit))],
        ).fetch_df()
    finally:
        conn.close()


def preview_query(sql: str, limit: int = DEFAULT_PREVIEW_LIMIT) -> pd.DataFrame:
    """Execute a SQL query and preview up to `limit` rows."""
    if limit <= 0:
        raise ValueError("limit must be a positive integer")

    conn = get_duckdb_connection(read_only=True)
    try:
        preview_sql = f"SELECT * FROM ({sql}) AS preview_query LIMIT ?"
        return conn.execute(preview_sql, [int(limit)]).fetch_df()
    finally:
        conn.close()


def list_tables() -> list[str]:
    """Return the user-visible table names in the default DuckDB database."""
    conn = get_duckdb_connection(read_only=True)
    try:
        rows = conn.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'main'
            ORDER BY table_name
            """
        ).fetchall()
    finally:
        conn.close()

    return [row[0] for row in rows]


def summarize_table(table_name: str) -> dict[str, Any]:
    """Return small metadata useful for UI summaries."""
    normalized_table = normalize_identifier(table_name)
    conn = get_duckdb_connection(read_only=True)
    try:
        row_count = conn.execute(
            f'SELECT COUNT(*) FROM "{normalized_table}"'
        ).fetchone()[0]
        columns = [
            row[0]
            for row in conn.execute(
                f'DESCRIBE "{normalized_table}"'
            ).fetchall()
        ]
    finally:
        conn.close()

    return {
        "table_name": normalized_table,
        "row_count": row_count,
        "columns": columns,
        "duckdb_path": get_duckdb_path(),
    }


def _apply_dataset_filter(sql_parts: list[str], params: list[Any], dataset_name: str | None) -> None:
    if dataset_name:
        sql_parts.append("AND da.dataset_name = ?")
        params.append(dataset_name)


def _apply_modality_filter(sql_parts: list[str], params: list[Any], modality: str | None) -> None:
    if not modality or modality == "all":
        return
    normalized = modality.strip().lower()
    if normalized in DOCUMENT_MODALITIES or normalized == "document":
        sql_parts.append("AND a.modality IN ('pdf', 'text', 'html')")
    else:
        sql_parts.append("AND a.modality = ?")
        params.append(normalized)


def _postprocess_path_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    if "source_path" in df.columns:
        df["filename"] = df["source_path"].apply(lambda value: os.path.basename(str(value)))
    for column in ["authors_json", "topics_json", "objects_json", "tags"]:
        if column in df.columns:
            df[column] = df[column].apply(
                lambda value: ", ".join(json.loads(value)) if value else ""
            )
    rename_map = {
        "authors_json": "authors",
        "topics_json": "topics",
        "objects_json": "objects",
    }
    df = df.rename(columns=rename_map)
    preferred = [
        column
        for column in [
            "dataset_name",
            "modality",
            "filename",
            "source_path",
            "title",
            "caption",
            "abstract",
            "summary",
            "description",
            "venue",
            "published_date",
            "authors",
            "topics",
            "objects",
            "ocr_text",
            "headline",
            "details",
            "tags",
        ]
        if column in df.columns
    ]
    remaining = [column for column in df.columns if column not in preferred]
    return df[preferred + remaining]


def preview_catalog(
    dataset_name: str | None = None,
    modality: str | None = None,
    limit: int = DEFAULT_PREVIEW_LIMIT,
) -> pd.DataFrame:
    """Preview catalog rows for one dataset or for the whole local catalog."""
    ensure_catalog_tables()
    params: list[Any] = []
    sql_parts = [
        """
        SELECT
            da.dataset_name,
            a.modality,
            da.alias_path AS source_path,
            dc.title,
            dc.abstract,
            dc.summary,
            dc.venue,
            dc.published_date,
            dc.authors_json,
            dc.topics_json,
            ic.caption,
            ic.description,
            ic.objects_json,
            ic.ocr_text
        FROM dataset_assets AS da
        JOIN assets AS a ON da.asset_id = a.asset_id
        LEFT JOIN document_catalog AS dc ON da.asset_id = dc.asset_id
        LEFT JOIN image_catalog AS ic ON da.asset_id = ic.asset_id
        WHERE 1 = 1
        """
    ]
    _apply_dataset_filter(sql_parts, params, dataset_name)
    _apply_modality_filter(sql_parts, params, modality)
    sql_parts.append("ORDER BY da.dataset_name, da.alias_path LIMIT ?")
    params.append(int(limit))

    conn = get_duckdb_connection(read_only=True)
    try:
        df = conn.execute("\n".join(sql_parts), params).fetch_df()
    finally:
        conn.close()
    return _postprocess_path_dataframe(df)


def search_catalog(
    query: str,
    dataset_name: str | None = None,
    modality: str | None = None,
    limit: int = DEFAULT_PREVIEW_LIMIT,
) -> pd.DataFrame:
    """Keyword search over cached catalog fields."""
    ensure_catalog_tables()
    pattern = f"%{query.strip().lower()}%"
    params: list[Any] = [pattern] * 8
    sql_parts = [
        """
        SELECT
            da.dataset_name,
            a.modality,
            da.alias_path AS source_path,
            COALESCE(dc.title, ic.caption, '') AS headline,
            COALESCE(dc.summary, dc.abstract, ic.description, ic.ocr_text, '') AS details,
            COALESCE(dc.topics_json, ic.topics_json, ic.objects_json, '') AS tags
        FROM dataset_assets AS da
        JOIN assets AS a ON da.asset_id = a.asset_id
        LEFT JOIN document_catalog AS dc ON da.asset_id = dc.asset_id
        LEFT JOIN image_catalog AS ic ON da.asset_id = ic.asset_id
        WHERE (
            lower(COALESCE(dc.title, '')) LIKE ?
            OR lower(COALESCE(dc.abstract, '')) LIKE ?
            OR lower(COALESCE(dc.summary, '')) LIKE ?
            OR lower(COALESCE(dc.venue, '')) LIKE ?
            OR lower(COALESCE(dc.topics_json, '')) LIKE ?
            OR lower(COALESCE(ic.caption, '')) LIKE ?
            OR lower(COALESCE(ic.description, '')) LIKE ?
            OR lower(COALESCE(ic.objects_json, '')) LIKE ?
        )
        """
    ]
    _apply_dataset_filter(sql_parts, params, dataset_name)
    _apply_modality_filter(sql_parts, params, modality)
    sql_parts.append("ORDER BY da.dataset_name, da.alias_path LIMIT ?")
    params.append(int(limit))

    conn = get_duckdb_connection(read_only=True)
    try:
        df = conn.execute("\n".join(sql_parts), params).fetch_df()
    finally:
        conn.close()
    return _postprocess_path_dataframe(df)


def cache_extraction_dataframe(
    dataset_name: str,
    schema_name: str,
    df: pd.DataFrame,
    schema_version: str = "v1",
    source_output_dataset: str | None = None,
) -> dict[str, Any]:
    """
    Persist a workload dataframe as reusable schema cache rows keyed by asset.

    The dataframe must contain a `filename` column so rows can be mapped back to
    registered dataset assets.
    """
    ensure_catalog_tables()
    if df.empty:
        return {
            "schema_name": schema_name,
            "cached_rows": 0,
            "skipped_rows": 0,
            "cached_assets": 0,
        }
    if "filename" not in df.columns:
        return {
            "schema_name": schema_name,
            "cached_rows": 0,
            "skipped_rows": int(len(df)),
            "cached_assets": 0,
            "reason": "filename column missing",
        }

    conn = get_duckdb_connection()
    try:
        lookup_rows = conn.execute(
            """
            SELECT asset_id, alias_path
            FROM dataset_assets
            WHERE dataset_name = ?
            """,
            [dataset_name],
        ).fetchall()
        filename_to_asset = {
            os.path.basename(str(alias_path)): asset_id
            for asset_id, alias_path in lookup_rows
        }

        cached_rows = 0
        skipped_rows = 0
        cached_assets: set[str] = set()
        deleted_assets: set[str] = set()

        for filename, group in df.groupby(df["filename"].astype(str), sort=False):
            asset_id = filename_to_asset.get(os.path.basename(filename))
            if asset_id is None:
                skipped_rows += int(len(group))
                continue

            if asset_id not in deleted_assets:
                conn.execute(
                    """
                    DELETE FROM derived_extractions
                    WHERE dataset_name = ?
                      AND asset_id = ?
                      AND schema_name = ?
                      AND schema_version = ?
                    """,
                    [dataset_name, asset_id, schema_name, schema_version],
                )
                deleted_assets.add(asset_id)

            cached_assets.add(asset_id)
            for row_index, (_, row) in enumerate(group.reset_index(drop=True).iterrows()):
                payload = {
                    column: _normalize_json_value(value)
                    for column, value in row.items()
                }
                conn.execute(
                    """
                    INSERT INTO derived_extractions (
                        dataset_name,
                        asset_id,
                        schema_name,
                        schema_version,
                        row_index,
                        source_output_dataset,
                        result_json,
                        result_columns_json,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        dataset_name,
                        asset_id,
                        schema_name,
                        schema_version,
                        row_index,
                        source_output_dataset,
                        _json_dumps(payload),
                        _json_dumps(list(group.columns)),
                        _utcnow(),
                    ],
                )
                cached_rows += 1
    finally:
        conn.close()

    return {
        "schema_name": schema_name,
        "cached_rows": cached_rows,
        "skipped_rows": skipped_rows,
        "cached_assets": len(cached_assets),
    }


def preview_cached_schema(
    dataset_name: str,
    schema_name: str,
    limit: int = DEFAULT_PREVIEW_LIMIT,
) -> pd.DataFrame:
    """Load cached schema results back into a dataframe for inspection."""
    ensure_catalog_tables()
    conn = get_duckdb_connection(read_only=True)
    try:
        rows = conn.execute(
            """
            SELECT
                d.asset_id,
                da.alias_path,
                d.row_index,
                d.result_json
            FROM derived_extractions AS d
            LEFT JOIN dataset_assets AS da
              ON da.dataset_name = d.dataset_name AND da.asset_id = d.asset_id
            WHERE d.dataset_name = ? AND d.schema_name = ?
            ORDER BY da.alias_path, d.row_index
            LIMIT ?
            """,
            [dataset_name, schema_name, int(limit)],
        ).fetchall()
    finally:
        conn.close()

    unpacked_rows = []
    for asset_id, alias_path, row_index, result_json in rows:
        payload = json.loads(result_json)
        payload["asset_id"] = asset_id
        payload["source_path"] = alias_path
        payload["filename"] = os.path.basename(str(alias_path)) if alias_path else None
        payload["cache_row_index"] = row_index
        unpacked_rows.append(payload)

    if not unpacked_rows:
        return pd.DataFrame()
    return pd.DataFrame(unpacked_rows)


def list_cached_schemas(dataset_name: str | None = None) -> pd.DataFrame:
    """Summarize reusable cached schema results."""
    ensure_catalog_tables()
    params: list[Any] = []
    sql_parts = [
        """
        SELECT
            dataset_name,
            schema_name,
            schema_version,
            COUNT(*) AS row_count,
            COUNT(DISTINCT asset_id) AS asset_count,
            MAX(updated_at) AS last_updated
        FROM derived_extractions
        WHERE 1 = 1
        """
    ]
    if dataset_name:
        sql_parts.append("AND dataset_name = ?")
        params.append(dataset_name)
    sql_parts.append(
        """
        GROUP BY dataset_name, schema_name, schema_version
        ORDER BY dataset_name, schema_name, schema_version
        """
    )

    conn = get_duckdb_connection(read_only=True)
    try:
        return conn.execute("\n".join(sql_parts), params).fetch_df()
    finally:
        conn.close()


def summarize_dataset_catalog(dataset_name: str) -> dict[str, Any]:
    """Return coverage metrics for one dataset's persisted catalog state."""
    ensure_catalog_tables()
    conn = get_duckdb_connection(read_only=True)
    try:
        counts = conn.execute(
            """
            SELECT
                COUNT(*) AS asset_count,
                COUNT(*) FILTER (WHERE a.index_status = 'indexed') AS indexed_asset_count,
                COUNT(*) FILTER (WHERE a.index_status = 'pending') AS pending_asset_count,
                COUNT(*) FILTER (WHERE a.index_status = 'error') AS error_asset_count
            FROM dataset_assets AS da
            JOIN assets AS a ON da.asset_id = a.asset_id
            WHERE da.dataset_name = ?
            """,
            [dataset_name],
        ).fetchone()
        document_rows = conn.execute(
            """
            SELECT COUNT(*)
            FROM dataset_assets AS da
            JOIN document_catalog AS dc ON da.asset_id = dc.asset_id
            WHERE da.dataset_name = ?
            """,
            [dataset_name],
        ).fetchone()[0]
        image_rows = conn.execute(
            """
            SELECT COUNT(*)
            FROM dataset_assets AS da
            JOIN image_catalog AS ic ON da.asset_id = ic.asset_id
            WHERE da.dataset_name = ?
            """,
            [dataset_name],
        ).fetchone()[0]
        content_rows = conn.execute(
            """
            SELECT COUNT(*)
            FROM dataset_assets AS da
            JOIN asset_contents AS ac ON da.asset_id = ac.asset_id
            WHERE da.dataset_name = ?
            """,
            [dataset_name],
        ).fetchone()[0]
        section_rows = conn.execute(
            """
            SELECT COUNT(*)
            FROM dataset_assets AS da
            JOIN document_sections AS ds ON da.asset_id = ds.asset_id
            WHERE da.dataset_name = ?
            """,
            [dataset_name],
        ).fetchone()[0]
    finally:
        conn.close()

    return {
        "dataset_name": dataset_name,
        "asset_count": int(counts[0] or 0),
        "indexed_asset_count": int(counts[1] or 0),
        "pending_asset_count": int(counts[2] or 0),
        "error_asset_count": int(counts[3] or 0),
        "document_catalog_rows": int(document_rows or 0),
        "image_catalog_rows": int(image_rows or 0),
        "asset_content_rows": int(content_rows or 0),
        "document_section_rows": int(section_rows or 0),
    }
