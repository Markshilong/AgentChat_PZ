#!/usr/bin/env python3
"""将本地 DuckDB 中的 catalog（数据集注册与索引元数据）打印到终端。

依赖环境变量 DUCKDB_PATH（未设置时与 bdf_pz.db.get_duckdb_path 行为一致）。

用法示例:
  python scripts/dump_catalog.py
  python scripts/dump_catalog.py --dataset papers --limit 20
  DUCKDB_PATH=/path/to/db.duckdb python scripts/dump_catalog.py --metrics-only
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# 以脚本方式运行时保证能 import bdf_pz
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

import pandas as pd

from bdf_pz.db import (
    get_duckdb_path,
    list_cached_schemas,
    load_persisted_datasets,
    preview_catalog,
    summarize_dataset_catalog,
)


def _format_df(df: pd.DataFrame, max_colwidth: int = 72) -> str:
    if df.empty:
        return "(无行)"
    with pd.option_context(
        "display.max_columns",
        None,
        "display.width",
        None,
        "display.max_colwidth",
        max_colwidth,
    ):
        return df.to_string(index=False)


def main() -> int:
    parser = argparse.ArgumentParser(description="输出 DuckDB 中的 catalog 信息")
    parser.add_argument(
        "--duckdb",
        metavar="PATH",
        help="覆盖 DUCKDB_PATH（仅此进程内生效）",
    )
    parser.add_argument(
        "-d",
        "--dataset",
        metavar="NAME",
        help="仅查看指定数据集",
    )
    parser.add_argument(
        "-m",
        "--modality",
        metavar="MOD",
        help="按资源类型过滤（如 pdf、text、image）",
    )
    parser.add_argument(
        "-n",
        "--limit",
        type=int,
        default=50,
        metavar="N",
        help="catalog 预览最多输出行数（默认 50）",
    )
    parser.add_argument(
        "--metrics-only",
        action="store_true",
        help="只打印每个数据集的索引统计，不输出明细表",
    )
    parser.add_argument(
        "--colwidth",
        type=int,
        default=72,
        metavar="W",
        help="表格单元格最大字符宽度（默认 72）",
    )
    parser.add_argument(
        "--cached-schemas",
        action="store_true",
        help="同时列出 derived_extractions 中的缓存 schema 摘要",
    )
    args = parser.parse_args()

    if args.duckdb:
        os.environ["DUCKDB_PATH"] = args.duckdb

    db_path = get_duckdb_path()
    print(f"DuckDB: {db_path}\n")

    datasets = load_persisted_datasets()
    names = sorted(datasets.keys())
    if args.dataset:
        if args.dataset not in names:
            print(
                f"警告: 数据库中未找到数据集 '{args.dataset}'。"
                f" 已知名称: {', '.join(names) if names else '(无)'}",
                file=sys.stderr,
            )
        scope_names = [args.dataset]
    else:
        scope_names = names

    print(f"## 已注册数据集 ({len(names)})\n")
    if not names:
        print("（无注册数据集；若刚使用过 notebook，请确认 DUCKDB_PATH 指向同一文件。）\n")
    else:
        rows = []
        for name in names:
            entry = datasets[name]
            path = entry.get("path") or entry.get("table_name") or entry.get("sql_query") or "-"
            if isinstance(path, str) and len(path) > 80:
                path = path[:77] + "..."
            rows.append(
                {"dataset_name": name, "source_type": entry.get("source_type", "-"), "path_or_note": path}
            )
        print(_format_df(pd.DataFrame(rows), args.colwidth))
        print()

    print("## Catalog 索引统计\n")
    metrics_rows: list[dict[str, str]] = []
    for name in scope_names:
        try:
            s = summarize_dataset_catalog(name)
        except Exception as exc:
            metrics_rows.append(
                {
                    "dataset": name,
                    "assets": "-",
                    "indexed": "-",
                    "pending": "-",
                    "errors": "-",
                    "catalog_rows": f"错误: {exc}",
                }
            )
            continue
        metrics_rows.append(
            {
                "dataset": name,
                "assets": str(s["asset_count"]),
                "indexed": str(s["indexed_asset_count"]),
                "pending": str(s["pending_asset_count"]),
                "errors": str(s["error_asset_count"]),
                "catalog_rows": f"doc={s['document_catalog_rows']} img={s['image_catalog_rows']}",
            }
        )
    print(_format_df(pd.DataFrame(metrics_rows), args.colwidth))
    print()

    if args.metrics_only:
        if args.cached_schemas:
            cs = list_cached_schemas(args.dataset)
            print("## 缓存的 extraction schema\n")
            print(_format_df(cs, args.colwidth))
        return 0

    print("## Catalog 明细预览\n")
    preview_df = preview_catalog(
        dataset_name=args.dataset,
        modality=args.modality,
        limit=args.limit,
    )
    print(_format_df(preview_df, args.colwidth))
    print(f"\n（共预览最多 {args.limit} 行；使用 -n 调整；使用 -d/-m 缩小范围。）\n")

    if args.cached_schemas:
        cs = list_cached_schemas(args.dataset)
        print("## 缓存的 extraction schema\n")
        print(_format_df(cs, args.colwidth))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
