#!/usr/bin/env python3
"""Compare two collected plan snapshots only where original SQL hashes match."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from plan_report import query_metrics, summarize
from report import write_csv, write_json


def compare(baseline: Path, indexed: Path, output: Path) -> dict[str, Any]:
    snapshots = []
    for directory in (baseline, indexed):
        manifest = json.loads((directory / "plan_manifest.json").read_text(encoding="utf-8"))
        data = json.loads((directory / "aggregate.json").read_text(encoding="utf-8"))
        entries = {entry["query_id"]: entry for entry in manifest["queries"]}
        reports = {qid: json.loads((directory / path).read_text(encoding="utf-8"))
                   for qid, path in data["query_reports"].items()}
        snapshots.append((manifest, data, entries, reports))
    base, target = snapshots
    rows, exclusions = [], []
    for query_id in sorted(base[2].keys() | target[2].keys()):
        if query_id not in base[2] or query_id not in target[2]:
            exclusions.append({"query_id": query_id, "reason": "Missing from one selection"})
            continue
        left_entry, right_entry = base[2][query_id], target[2][query_id]
        if not left_entry.get("source_sha256") or left_entry["source_sha256"] != right_entry.get("source_sha256"):
            exclusions.append({"query_id": query_id, "reason": "SQL hashes differ or are missing"})
            continue
        left, right = base[3][query_id], target[3][query_id]
        if left["status"] == "failed" or right["status"] == "failed":
            exclusions.append({"query_id": query_id, "reason": "Failed plan in at least one snapshot"})
            continue
        row: dict[str, Any] = {"query_id": query_id, "sql_sha256": left_entry["source_sha256"]}
        for label, report in (("baseline", left), ("indexed", right)):
            costs = [s["estimated_subtree_cost"] for s in report["statements"]]
            # Only a single statement's root cost is compared; do not add overlapping subtree costs.
            row[f"{label}_estimated_cost"] = costs[0] if len(costs) == 1 else None
            row[f"{label}_strategies"] = summarize(report["join_operators"])["physical_operator_summary"]
            row[f"{label}_join_operator_count"] = len(report["join_operators"])
            row[f"{label}_early_abort_reasons"] = [s["optimization_early_abort_reason"]
                                                   for s in report["statements"]
                                                   if s["optimization_early_abort_reason"]]
            metrics = query_metrics(report)
            for key in ("actual_elapsed_ms", "actual_cpu_ms", "worst_row_error_factor",
                        "worst_row_error_unbounded", "worst_row_deviation_pct"):
                row[f"{label}_{key}"] = metrics[key]
        rows.append(row)
    result = {
        "baseline_database": base[0]["database"], "indexed_database": target[0]["database"],
        "baseline_collected_at_utc": base[0]["collected_at_utc"],
        "indexed_collected_at_utc": target[0]["collected_at_utc"],
        "matched_queries": len(rows), "exclusions": exclusions, "queries": rows,
        "baseline_coverage": base[1]["coverage"], "indexed_coverage": target[1]["coverage"],
        "baseline_strategy_summary": base[1]["plan_evidence"]["all_operators"]["physical_operator_summary"],
        "indexed_strategy_summary": target[1]["plan_evidence"]["all_operators"]["physical_operator_summary"],
        "baseline_mode": base[0]["mode"], "indexed_mode": target[0]["mode"],
    }
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "comparison.json", result)
    write_csv(output / "query_plan_comparison.csv", rows, [
        "query_id", "sql_sha256", "baseline_estimated_cost", "indexed_estimated_cost",
        "baseline_strategies", "indexed_strategies", "baseline_join_operator_count",
        "indexed_join_operator_count", "baseline_early_abort_reasons", "indexed_early_abort_reasons",
        "baseline_actual_elapsed_ms", "indexed_actual_elapsed_ms", "baseline_actual_cpu_ms", "indexed_actual_cpu_ms",
        "baseline_worst_row_error_factor", "indexed_worst_row_error_factor",
        "baseline_worst_row_error_unbounded", "indexed_worst_row_error_unbounded",
        "baseline_worst_row_deviation_pct", "indexed_worst_row_deviation_pct",
    ])
    summary = [
        "# Baseline versus indexed plan evidence", "",
        f"Databases: **{base[0]['database']}** and **{target[0]['database']}**. "
        f"Queries with matching SQL hashes and valid plans: **{len(rows)}**. Excluded: **{len(exclusions)}**.",
        "", f"Collection modes: `{base[0]['mode']}` / `{target[0]['mode']}`. "
        "Estimated collections compile only; actual collections execute each SELECT once and report "
        "server elapsed/CPU time and row-estimate deviations. These diagnostic executions are **not a "
        "controlled repeated performance benchmark**. The index inventory and statistics timestamps are retained "
        "in each snapshot's plan_manifest.json. Database contents were not hashed/compared, so SQL hash "
        "equality alone does not prove the two databases contain identical data.",
        "", "[Per-query comparison CSV](query_plan_comparison.csv) | "
        "[Machine-readable comparison](comparison.json) | "
        f"[Baseline graph]({Path(os.path.relpath(baseline / 'join_graph.html', output)).as_posix()}) | "
        f"[Indexed graph]({Path(os.path.relpath(indexed / 'join_graph.html', output)).as_posix()})", "",
        "## Physical operator counts in the compiled plan trees", "",
        "| Strategy | Baseline | Indexed |", "|---|---:|---:|",
    ]
    strategies = sorted(result["baseline_strategy_summary"].keys() | result["indexed_strategy_summary"].keys())
    for strategy in strategies:
        summary.append(f"| {strategy} | {result['baseline_strategy_summary'].get(strategy, 0)} | "
                       f"{result['indexed_strategy_summary'].get(strategy, 0)} |")
    summary.extend([
        "", "Adaptive alternatives can coexist in these trees. Counts are not the number of "
        "operators that will necessarily execute. Changing join order, operator elimination and "
        "intermediate aggregation also change which physical pairs can be mapped.",
        "", "## Highest baseline optimizer-cost queries", "",
        "Root statement costs are optimizer units, **not seconds**. No sums over overlapping "
        "operator subtrees are used.", "",
        "| Query | Baseline estimated cost | Indexed estimated cost | Optimizer early-abort reasons (baseline / indexed) |",
        "|---|---:|---:|---|",
    ])
    top = sorted((r for r in rows if r["baseline_estimated_cost"] is not None),
                 key=lambda r: (-r["baseline_estimated_cost"], r["query_id"]))[:12]
    for row in top:
        summary.append(f"| {row['query_id']} | {row['baseline_estimated_cost']:g} | "
                       f"{row['indexed_estimated_cost'] if row['indexed_estimated_cost'] is not None else 'n/a'} | "
                       f"{', '.join(row['baseline_early_abort_reasons']) or '-'} / "
                       f"{', '.join(row['indexed_early_abort_reasons']) or '-'} |")
    if base[0]["mode"] == target[0]["mode"] == "actual_STATISTICS_XML":
        summary.extend([
            "", "## Diagnostic actual elapsed time (not optimizer cost)", "",
            "Only completed, SQL-hash-matched pairs are compared. Timed-out or failed queries are "
            "excluded here but remain explicit in each snapshot's coverage and query metrics.", "",
            "| Query | Baseline elapsed ms | Indexed elapsed ms |",
            "|---|---:|---:|",
        ])
        for row in sorted(rows, key=lambda r: (-(r["baseline_actual_elapsed_ms"] or 0), r["query_id"]))[:15]:
            summary.append(f"| {row['query_id']} | {row['baseline_actual_elapsed_ms']} | "
                           f"{row['indexed_actual_elapsed_ms']} |")
    summary.extend([
        "", "## Recommended next steps for this benchmark", "",
        "1. Use the original [syntax graph](../static/join_graph.html) for workload frequencies. "
        "Use plan edge details for estimated rows/strategies and query reports for whole-plan context.",
        "2. Start with the previously slow query_015 and query_082. Inspect their raw plans and "
        "index access paths in both databases; optimizer TimeOut is search-budget termination, "
        "not a wall-clock timeout or proof that a query was executed.",
        "3. A common join key, a large intermediate estimate, or a lower estimated cost is a hypothesis, "
        "not sufficient evidence to create an index. Verify key order and existing indexes, selective "
        "filters and missing/incorrect cardinality estimates first.",
        "4. Check baseline/indexed data and statistics comparability, then run the existing focused profiler "
        "under a deliberate runtime limit if needed. Actual plans and logical reads can establish whether "
        "estimates, spills and chosen adaptive branches agree with reality.",
        "5. Only after focused evidence, compare the unchanged full benchmark on both databases. "
        "Do not time concurrent plan collection, index creation or statistics maintenance.",
        "", "No indexes or benchmark scripts were changed by this analysis.", "",
    ])
    (output / "README.md").write_text("\n".join(summary), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--indexed", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = compare(args.baseline, args.indexed, args.output)
    print(f"Matched {result['matched_queries']} queries; excluded {len(result['exclusions'])}")
