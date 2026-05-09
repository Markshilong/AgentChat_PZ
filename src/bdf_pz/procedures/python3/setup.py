import palimpzest as pz
import pandas as pd
import time
import os
import IPython

# formatter = IPython.get_ipython().display_formatter.formatters['text/plain']
# formatter.max_seq_length = 0

# set model provider environment variables based on container env
os.environ["OPENAI_API_KEY"] = "{{ OPENAI_API_KEY }}" 
os.environ["ANTHROPIC_API_KEY"] = "{{ ANTHROPIC_API_KEY }}"

# Represents a scientific research paper, which in practice is usually from a PDF file
scientific_paper_schema = [
    {"name": "paper_title", "type": str, "desc": "The title of the paper. This is a natural language title, not a number or letter."},
    {"name": "author", "type": str, "desc": "The name of the first author of the paper"},
    {"name": "abstract", "type": str, "desc": "A short description of the paper contributions and findings"},
]

reference_schema = [
    {"name": "index", "type": int, "desc": "The index of the reference in the paper."},
    {"name": "title", "type": str, "desc": "The title of the paper being cited."},
    {"name": "first_author", "type": str, "desc": "The author of the paper being cited."},
    {"name": "year", "type": int, "desc": "The year in which the cited paper was published."},
]

image_schema = [
    {"name": "caption", "type": str, "desc": "A one-sentence natural-language caption describing what the image shows."},
    {"name": "description", "type": str, "desc": "A longer paragraph describing the visible contents of the image, including subjects, setting, and notable details."},
    {"name": "objects", "type": str, "desc": "A comma-separated list of distinct objects, people, or entities visible in the image."},
    {"name": "ocr_text", "type": str, "desc": "Any readable text that appears in the image, transcribed verbatim. Empty string if there is no text."},
]

DATA_PATH = "testdata/"
# print("Setup complete")

registered_datasets = {}
for name in os.listdir(DATA_PATH):
    registered_datasets[name] = os.path.join(DATA_PATH, name)

existing_schemas = {"ScientificPaper":scientific_paper_schema,
                    "Reference":reference_schema,
                    "Image":image_schema}

demo_manifest_candidates = [
    "/home/user/testdata/structured-data/paper_manifest.csv",
    "testdata/structured-data/paper_manifest.csv",
]
demo_manifest_path = next(
    (candidate for candidate in demo_manifest_candidates if os.path.exists(candidate)),
    None,
)

if demo_manifest_path is not None:
    try:
        import duckdb

        duckdb_path = os.environ.get("DUCKDB_PATH", "/tmp/bdf_pz.duckdb")
        conn = duckdb.connect(duckdb_path)
        try:
            conn.execute("DROP VIEW IF EXISTS paper_manifest_2021_onward")
            conn.execute("DROP TABLE IF EXISTS paper_manifest")
            conn.execute(
                "CREATE TABLE paper_manifest AS SELECT * FROM read_csv_auto(?)",
                [demo_manifest_path],
            )
            conn.execute(
                """
                CREATE OR REPLACE VIEW paper_manifest_2021_onward AS
                SELECT paper_id, publication_year, pdf_path
                FROM paper_manifest
                WHERE publication_year >= 2021
                ORDER BY publication_year, paper_id
                """
            )
        finally:
            conn.close()
    except Exception:
        pass
