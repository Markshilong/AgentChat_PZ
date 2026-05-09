from __future__ import annotations

import pandas as pd

from bdf_pz.db import (
    FULL_TEXT_CONTENT_KIND,
    cache_extraction_dataframe,
    import_delimited_file,
    list_cached_schemas,
    mark_asset_index_status,
    preview_cached_schema,
    preview_catalog,
    preview_table,
    register_dataset_assets,
    replace_asset_content,
    replace_document_sections,
    search_catalog,
    split_text_into_sections,
    upsert_document_catalog,
    upsert_image_catalog,
)


def test_document_catalog_and_schema_cache_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKDB_PATH", str(tmp_path / "catalog.duckdb"))

    dataset_dir = tmp_path / "papers"
    dataset_dir.mkdir()
    paper_path = dataset_dir / "paper1.txt"
    paper_path.write_text(
        "ABSTRACT\nThis is a short document.\n\nMETHODS\nWe tested the cache.",
        encoding="utf-8",
    )

    descriptors = register_dataset_assets("papers", [str(paper_path)], source_type="file_path")
    assert len(descriptors) == 1

    asset_id = descriptors[0]["asset_id"]
    upsert_document_catalog(
        asset_id=asset_id,
        title="Paper One",
        abstract="A document about caches.",
        authors=["Alice", "Bob"],
        published_date="2024",
        venue="SIGMOD",
        topics=["cache", "duckdb"],
        summary="Short summary.",
    )
    replace_asset_content(asset_id, FULL_TEXT_CONTENT_KIND, paper_path.read_text(encoding="utf-8"))
    replace_document_sections(
        asset_id,
        split_text_into_sections(paper_path.read_text(encoding="utf-8")),
    )
    mark_asset_index_status([asset_id], "indexed")

    catalog_df = preview_catalog(dataset_name="papers")
    assert list(catalog_df["title"]) == ["Paper One"]
    assert "Alice" in catalog_df.iloc[0]["authors"]

    search_df = search_catalog("duckdb", dataset_name="papers")
    assert len(search_df) == 1
    assert search_df.iloc[0]["headline"] == "Paper One"

    cache_summary = cache_extraction_dataframe(
        dataset_name="papers",
        schema_name="TopicSchema",
        df=pd.DataFrame(
            [
                {"filename": "paper1.txt", "topic": "duckdb", "score": 0.9},
                {"filename": "paper1.txt", "topic": "cache", "score": 0.8},
            ]
        ),
        source_output_dataset="topic_results",
    )
    assert cache_summary["cached_rows"] == 2
    assert cache_summary["cached_assets"] == 1

    cached_schema_df = list_cached_schemas(dataset_name="papers")
    assert len(cached_schema_df) == 1
    assert cached_schema_df.iloc[0]["schema_name"] == "TopicSchema"

    cached_rows_df = preview_cached_schema(dataset_name="papers", schema_name="TopicSchema")
    assert set(cached_rows_df["topic"]) == {"duckdb", "cache"}
    assert set(cached_rows_df["filename"]) == {"paper1.txt"}


def test_image_catalog_and_structured_import(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKDB_PATH", str(tmp_path / "structured.duckdb"))

    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_path = image_dir / "image1.jpg"
    image_path.write_bytes(b"fake-image-bytes")

    descriptors = register_dataset_assets("images", [str(image_path)], source_type="file_path")
    assert len(descriptors) == 1
    asset_id = descriptors[0]["asset_id"]

    upsert_image_catalog(
        asset_id=asset_id,
        caption="Microscopy image",
        description="A bright microscopy view with labeled cells.",
        objects=["cells", "labels"],
        ocr_text="CELL A",
        topics=["biology"],
    )
    mark_asset_index_status([asset_id], "indexed")

    image_catalog_df = preview_catalog(dataset_name="images", modality="image")
    assert list(image_catalog_df["caption"]) == ["Microscopy image"]

    csv_path = tmp_path / "manifest.csv"
    csv_path.write_text("paper_id,year\np1,2024\np2,2025\n", encoding="utf-8")
    table_name = import_delimited_file(str(csv_path), "manifest_table", if_exists="replace")
    imported_df = preview_table(table_name)
    assert len(imported_df) == 2
    assert set(imported_df["paper_id"]) == {"p1", "p2"}
