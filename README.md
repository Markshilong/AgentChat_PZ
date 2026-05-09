# bdf-pz

[![PyPI - Version](https://img.shields.io/pypi/v/bdf-pz.svg)](https://pypi.org/project/bdf-pz)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/bdf-pz.svg)](https://pypi.org/project/bdf-pz)

-----

## Table of Contents

- [Installation](#installation)
- [License](#license)

## Installation

```console
pip install -e .
pip install git+https://github.com/mitdbg/palimpzest.git@main
export OPENAI_API_KEY=your key here
```

For local setup, first create your env/config files from the examples:

```console
cp env.example .env
cp .beaker.conf.example .beaker.conf
```

Then populate the Anthropic key fields:

- in `.env`, set:
  - `ANTHROPIC_API_KEY=...`
  - `LLM_SERVICE_TOKEN=...`
- in `.beaker.conf`, set:
  - `llm_service_token = "..."`
  - `[providers.anthropic].api_key = "..."`

For the MVP structured-data path, DuckDB is used as the default local database sink/source. By default the app expects a `DUCKDB_PATH` such as:

```console
export DUCKDB_PATH=/jupyter/.data/bdf_pz.duckdb
```

## Running
Run with `beaker notebook`.


## Running in Docker
Ensure you have set up your `.env` file then run

```
docker compose build
docker compose up
```

The Docker setup mounts `./.data` into the container and uses it for the default DuckDB database path, so structured outputs can persist across container restarts.

## License

`bdf-pz` is distributed under the terms of the [MIT](https://spdx.org/licenses/MIT.html) license.
