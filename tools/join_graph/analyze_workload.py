#!/usr/bin/env python3
"""Analyze a file-list-defined T-SQL workload without a database connection."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot.errors import SqlglotError

from extractor import extract, read_schema
from report import export, report_filename
from usage import DEFAULT_DATASET, DEFAULT_INDEX_SCRIPT, count_rows, read_indexes

LOGGER = logging.getLogger("join_graph")
ROOT = Path(__file__).resolve().parents[2]


def discover(input_dir: Path, query_list: Path | None) -> tuple[list[tuple[str, Path]], dict[str, Any]]:
    if not input_dir.is_dir():
        raise ValueError(f"Input directory does not exist: {input_dir}")
    if query_list is None and (input_dir / "queries.lst").is_file():
        query_list = input_dir / "queries.lst"
    all_sql = sorted(p for p in input_dir.rglob("*") if p.is_file() and p.suffix.lower() == ".sql")
    warnings = []
    selected = {}
    if query_list is not None:
        for line_number, line in enumerate(query_list.read_text(encoding="utf-8-sig").splitlines(), 1):
            name = line.strip()
            if not name:
                continue
            path = (input_dir / name).resolve()
            if not path.is_relative_to(input_dir) or path.suffix.lower() != ".sql":
                raise ValueError(f"Invalid query list entry on line {line_number}: {name!r}")
            query_id = path.relative_to(input_dir).as_posix()
            if query_id in selected:
                warnings.append(f"Duplicate list entry ignored: {query_id}")
            selected[query_id] = path
    else:
        selected = {p.relative_to(input_dir).as_posix(): p for p in all_sql if p.name.lower() != "benchmark.sql"}
    for warning in warnings:
        LOGGER.warning(warning)
    manifest = {
        "selection": "query_list" if query_list else "recursive_sql",
        "input": input_dir.as_posix(),
        "query_list": query_list.as_posix() if query_list else None,
        "query_list_sha256": hashlib.sha256(query_list.read_bytes()).hexdigest() if query_list else None,
        "warnings": warnings,
        "excluded_sql_files": [p.relative_to(input_dir).as_posix() for p in all_sql
                               if p.relative_to(input_dir).as_posix() not in selected],
        "extractor": {"name": "sqlglot", "version": sqlglot.__version__, "dialect": "tsql"},
        "queries": [],
    }
    return sorted(selected.items()), manifest


def run(input_dir: Path, output: Path, schema_path: Path | None,
        query_list: Path | None = None, minimum: int = 5,
        index_script: Path | None = None, dataset: Path | None = None) -> dict[str, Any]:
    input_dir, output = input_dir.resolve(), output.resolve()
    if output == input_dir or output.is_relative_to(input_dir):
        raise ValueError("Output must be outside the input directory to avoid analyzing generated files")
    selected, manifest = discover(input_dir, query_list)
    schema = read_schema(schema_path)
    reports = []
    for query_id, path in selected:
        source = None
        sha256 = None
        try:
            raw = path.read_bytes()
            sha256 = hashlib.sha256(raw).hexdigest()
            source = raw.decode("utf-8-sig")
            report = extract(source, query_id, schema)
        except (OSError, UnicodeError, SqlglotError, ValueError) as error:
            LOGGER.error("%s: %s", query_id, error)
            report = {"query_id": query_id, "status": "failed", "error": str(error),
                      "original_sql": source, "references": [], "joins": [], "unresolved": []}
        report["source_sha256"] = sha256
        reports.append(report)
        manifest["queries"].append({
            "query_id": query_id, "relative_path": query_id, "sha256": sha256,
            "status": report["status"], "report": f"query_reports/{report_filename(query_id)}",
        })
        LOGGER.info("%s: %s (%d joins, %d unresolved)", query_id, report["status"],
                    len(report["joins"]), len(report["unresolved"]))
    metadata = {
        "extractor": manifest["extractor"],
        "schema_file": str(schema_path) if schema_path else None,
        "schema_sha256": hashlib.sha256(schema_path.read_bytes()).hexdigest() if schema_path else None,
        "schema_tables": len(schema),
    }
    usage_context = {
        "indexes": read_indexes(index_script, schema_path), "row_counts": count_rows(dataset),
        "index_script": str(index_script) if index_script else None,
        "dataset": str(dataset) if dataset else None,
    }
    return export(output, reports, manifest, minimum, metadata, usage_context)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "data/1GB/queryset")
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("generated") / "static")
    parser.add_argument("--query-list", type=Path, help="Defaults to INPUT/queries.lst if present")
    parser.add_argument("--schema", type=Path, default=ROOT / "tpcds-kit/tools/tpcds.sql",
                        help="CREATE TABLE DDL for resolving bare columns; defaults to TPC-DS schema")
    parser.add_argument("--no-schema", action="store_true", help="Use scoped aliases only (bare columns may be ambiguous)")
    parser.add_argument("--index-script", type=Path, default=DEFAULT_INDEX_SCRIPT,
                        help="CREATE INDEX script used to report existing index coverage in hints")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET,
                        help="Directory of dsdgen .dat files used for row counts (skipped if missing)")
    parser.add_argument("--min-edge-query-count", type=int, default=5,
                        help="Initial HTML filter and SVG threshold; CSV/JSON/DOT retain all edges")
    parser.add_argument("--fail-on-parse-errors", action="store_true",
                        help="Write all outputs, then exit 1 if any selected query failed")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s: %(message)s")
    if args.min_edge_query_count < 0:
        parser.error("--min-edge-query-count must be nonnegative")
    try:
        data = run(args.input, args.output, None if args.no_schema else args.schema,
                   args.query_list, args.min_edge_query_count, args.index_script, args.dataset)
    except (OSError, UnicodeError, SqlglotError, ValueError) as error:
        LOGGER.error("%s", error)
        return 2
    coverage = data["coverage"]
    print(json.dumps({key: coverage[key] for key in (
        "discovered_queries", "successful_queries", "partial_queries", "failed_queries",
        "physical_tables", "physical_column_relationships", "unresolved_records",
    )}, indent=2))
    print(f"Graph: {args.output.resolve() / 'join_graph.html'}")
    if coverage["partial_queries"]:
        LOGGER.warning("%d partial queries; inspect unresolved_joins.csv", coverage["partial_queries"])
    return int(args.fail_on_parse_errors and coverage["failed_queries"] > 0)


if __name__ == "__main__":
    sys.exit(main())
