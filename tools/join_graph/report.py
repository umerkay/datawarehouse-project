"""Deterministic aggregation and portable exports, independent of the parser."""

from __future__ import annotations

import csv
import hashlib
import html
import json
import math
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def stable_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def report_filename(query_id: str) -> str:
    return f"{stable_id(query_id)}.json"


def canonical(join: dict[str, Any]) -> tuple[str, str, str, str, str, str]:
    left = (join["left"]["table"], join["left"]["column"])
    right = (join["right"]["table"], join["right"]["column"])
    operator = join["operator"]
    if right < left:
        left, right = right, left
        operator = {">": "<", ">=": "<=", "<": ">", "<=": ">="}.get(operator, operator)
    return (*left, *right, join["relationship_class"], operator)


def edge_label(edge: dict[str, Any]) -> str:
    def endpoint(side: str) -> str:
        return edge[f"{side}_table"] + (f".{edge[f'{side}_column']}" if edge[f"{side}_column"] else "")
    return f"{endpoint('left')} {edge['operator']} {endpoint('right')}"


def aggregate(reports: list[dict[str, Any]]) -> dict[str, Any]:
    edge_groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    references: dict[str, dict[str, Any]] = {}
    query_references = []
    for report in sorted(reports, key=lambda r: r["query_id"]):
        seen = set()
        for reference in report["references"]:
            table = reference["table"]
            node = references.setdefault(table, {
                "table": table, "reference_type": reference["reference_type"],
                "queries": set(), "join_queries": set(), "occurrences": 0, "neighbors": set(),
            })
            node["queries"].add(report["query_id"])
            if table not in seen:
                query_references.append({"query_id": report["query_id"],
                                         **{k: reference[k] for k in
                                            ("table", "reference_type", "resolution_status")}})
                seen.add(table)
        for join in report["joins"]:
            edge_groups[canonical(join)].append(join)
            for table in {join["left"]["table"], join["right"]["table"]}:
                node = references[table]
                node["join_queries"].add(report["query_id"])
                node["occurrences"] += 1
                other = {join["left"]["table"], join["right"]["table"]} - {table}
                node["neighbors"].update(other)
    edges = []
    pair_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for key, joins in sorted(edge_groups.items()):
        left_table, left_column, right_table, right_column, relation, operator = key
        query_ids = sorted({j["query_id"] for j in joins})
        edge = {
            "edge_id": stable_id(json.dumps(key)), "left_table": left_table,
            "left_column": left_column, "right_table": right_table, "right_column": right_column,
            "relationship_class": relation, "operator": operator,
            "query_count": len(query_ids), "occurrence_count": len(joins),
            "query_frequency": len(query_ids) / len(reports) if reports else 0,
            "join_type_summary": dict(sorted(Counter(j["join_type"] for j in joins).items())),
            "example_query_ids": query_ids[:10], "query_ids": query_ids,
            "resolution_status": "exact" if all(j["resolution_status"] == "exact" for j in joins) else "unresolved",
            "self_join": left_table == right_table,
            "conditional_occurrence_count": sum(j["conditional"] for j in joins),
        }
        edge["label"] = edge_label(edge)
        edges.append(edge)
        pair_groups[(left_table, right_table)].append(edge)
    pairs = []
    for (left, right), group in sorted(pair_groups.items()):
        queries = sorted({q for e in group for q in e["query_ids"]})
        types: Counter[str] = Counter()
        for edge in group:
            types.update(edge["join_type_summary"])
        pairs.append({
            "edge_id": stable_id(json.dumps([left, right])), "left_table": left, "right_table": right,
            "query_count": len(queries), "occurrence_count": sum(e["occurrence_count"] for e in group),
            "query_frequency": len(queries) / len(reports) if reports else 0,
            "relationship_count": len(group), "join_type_summary": dict(sorted(types.items())),
            "joined_columns": [e["label"] for e in group],
            "edge_ids": [e["edge_id"] for e in group],
            "query_ids": queries, "example_query_ids": queries[:10],
            "resolution_status": "exact" if all(e["resolution_status"] == "exact" for e in group) else "unresolved",
            "self_join": left == right,
        })
    nodes = [{
        "table": table, "reference_type": node["reference_type"],
        "query_count": len(node["queries"]), "join_query_count": len(node["join_queries"]),
        "join_occurrence_count": node["occurrences"], "degree": len(node["neighbors"]),
    } for table, node in sorted(references.items())]
    return {"nodes": nodes, "edges": edges, "table_pairs": pairs,
            "query_table_references": sorted(query_references, key=lambda r: (r["query_id"], r["table"]))}


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=True) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, sort_keys=True) if isinstance(value, (list, dict))
                             else value for key, value in row.items() if key in fields})


def physical_graph(data: dict[str, Any], minimum: int, pairs: bool = False) -> tuple[list, list]:
    nodes = [n for n in data["nodes"] if n["reference_type"] == "physical"]
    edges = [e for e in data["table_pairs" if pairs else "edges"]
             if e["resolution_status"] == "exact" and e["query_count"] >= minimum]
    return nodes, edges


def render_dot(data: dict[str, Any], minimum: int) -> str:
    nodes, edges = physical_graph(data, minimum)
    quote = json.dumps
    lines = ["graph joins {", '  graph [overlap=false, splines=true];',
             '  node [shape=ellipse, style=filled, fillcolor="#dbeafe"];']
    for node in nodes:
        lines.append(f'  {quote(node["table"])} [label={quote(node["table"] + chr(10) + str(node["query_count"]) + " queries")}];')
    for edge in edges:
        label = edge["label"] + f' ({edge["query_count"]} queries)'
        lines.append(f'  {quote(edge["left_table"])} -- {quote(edge["right_table"])} '
                     f'[label={quote(label)}, penwidth={1 + math.sqrt(edge["query_count"]):.2f}];')
    return "\n".join(lines + ["}", ""])


def render_svg(data: dict[str, Any], minimum: int) -> str:
    nodes, edges = physical_graph(data, minimum, pairs=True)
    positions = {}
    facts = [n for n in nodes if n["table"].endswith(("_sales", "_returns"))
             or n["table"].split(".")[-1] == "inventory"]
    dimensions = [n for n in nodes if n not in facts]
    for ring, radius in ((facts, 205), (dimensions, 465)):
        for i, node in enumerate(ring):
            angle = 2 * math.pi * i / len(ring) - math.pi / 2
            positions[node["table"]] = (650 + radius * math.cos(angle), 570 + radius * math.sin(angle))
    lines = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1300 1140">',
             '<rect width="1300" height="1140" fill="#f8fafc"/>',
             '<g font-family="sans-serif" text-anchor="middle">',
             '<text x="650" y="35" font-size="24">TPC-DS physical-table join topology</text>',
             f'<text x="650" y="65">Table pairs in at least {minimum} queries; widths and labels = distinct query count</text>',
             '<text x="650" y="89">Inner ring: facts; outer ring: dimensions. Use HTML for joined columns and evidence.</text>']
    for edge in sorted(edges, key=lambda e: e["query_count"]):
        x1, y1 = positions[edge["left_table"]]
        x2, y2 = positions[edge["right_table"]]
        title = html.escape(f'{edge["left_table"]} -- {edge["right_table"]}: {edge["query_count"]} queries; '
                            + "; ".join(edge["joined_columns"]))
        path = (f"M{x1},{y1} C{x1-90},{y1-110} {x1+90},{y1-110} {x1},{y1}"
                if edge["self_join"] else f"M{x1},{y1} L{x2},{y2}")
        lines.append(f'<path d="{path}" fill="none" stroke="#94a3b8" opacity=".65" '
                     f'stroke-width="{1 + math.sqrt(edge["query_count"]):.2f}"><title>{title}</title></path>')
        if not edge["self_join"]:
            lines.append(f'<text x="{(x1+x2)/2:.2f}" y="{(y1+y2)/2:.2f}" font-size="12" '
                         f'paint-order="stroke" stroke="#f8fafc" stroke-width="4">{edge["query_count"]}</text>')
    for node in nodes:
        x, y = positions[node["table"]]
        radius = 16 + math.sqrt(node["query_count"]) * 1.9
        fill = "#fbbf24" if node in facts else "#93c5fd"
        lines.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{radius:.2f}" fill="{fill}" stroke="#334155">'
                     f'<title>{html.escape(node["table"])}: {node["query_count"]} queries</title></circle>')
        lines.append(f'<text x="{x:.2f}" y="{y+radius+17:.2f}" font-size="15" '
                     f'paint-order="stroke" stroke="#f8fafc" stroke-width="4">{html.escape(node["table"])}</text>')
    return "\n".join(lines + ["</g></svg>", ""])


def export(output: Path, reports: list[dict[str, Any]], manifest: dict[str, Any],
           minimum: int, schema_metadata: dict[str, Any]) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    query_dir = output / "query_reports"
    query_dir.mkdir(exist_ok=True)
    previous_manifest = output / "manifest.json"
    if previous_manifest.is_file():
        previous = json.loads(previous_manifest.read_text(encoding="utf-8"))
        stale = {report_filename(q["query_id"]) for q in previous["queries"]}
        stale -= {report_filename(r["query_id"]) for r in reports}
        for filename in sorted(stale):
            (query_dir / filename).unlink(missing_ok=True)
    for report in reports:
        write_json(query_dir / report_filename(report["query_id"]), report)
    data = aggregate(reports)
    unresolved = [u for r in reports for u in r["unresolved"]]
    failures = [{"query_id": r["query_id"], "error": r["error"]}
                for r in reports if r["status"] == "failed"]
    counts = Counter(r["status"] for r in reports)
    coverage = {
        "discovered_queries": len(reports), "successful_queries": counts["success"],
        "partial_queries": counts["partial"], "failed_queries": counts["failed"],
        "parsed_queries": counts["success"] + counts["partial"],
        "frequency_denominator": len(reports),
        "frequency_definition": "distinct query count / all selected workload queries, including failures",
        "join_occurrences": sum(len(r["joins"]) for r in reports),
        "physical_join_occurrences": sum(j["resolution_status"] == "exact" for r in reports for j in r["joins"]),
        "unresolved_records": len(unresolved),
        "unresolved_reasons": dict(sorted(Counter(u["reason"] for u in unresolved).items())),
        "physical_tables": sum(n["reference_type"] == "physical" for n in data["nodes"]),
        "column_relationships": len(data["edges"]),
        "physical_column_relationships": sum(e["resolution_status"] == "exact" for e in data["edges"]),
        "physical_table_pairs": sum(e["resolution_status"] == "exact" for e in data["table_pairs"]),
        "queries": [{"query_id": r["query_id"], "status": r["status"],
                     "joins": len(r["joins"]), "unresolved": len(r["unresolved"]),
                     "report": f"query_reports/{report_filename(r['query_id'])}"} for r in reports],
        **schema_metadata,
    }
    data["coverage"] = coverage
    data["query_reports"] = {r["query_id"]: f"query_reports/{report_filename(r['query_id'])}" for r in reports}
    write_json(output / "manifest.json", manifest)
    write_json(output / "coverage_report.json", coverage)
    write_json(output / "aggregate.json", data)
    with (output / "normalized_joins.jsonl").open("w", encoding="utf-8") as file:
        for report in reports:
            for join in report["joins"]:
                file.write(json.dumps(join, sort_keys=True) + "\n")
    write_csv(output / "join_edges.csv", data["edges"], [
        "edge_id", "left_table", "left_column", "right_table", "right_column", "relationship_class",
        "operator", "query_count", "occurrence_count", "query_frequency", "join_type_summary",
        "example_query_ids", "query_ids", "resolution_status", "self_join", "conditional_occurrence_count",
    ])
    write_csv(output / "table_pair_edges.csv", data["table_pairs"], [
        "edge_id", "left_table", "right_table", "query_count", "occurrence_count", "query_frequency",
        "relationship_count", "joined_columns", "join_type_summary", "query_ids", "resolution_status", "self_join",
    ])
    write_csv(output / "table_nodes.csv", data["nodes"], [
        "table", "reference_type", "query_count", "join_query_count", "join_occurrence_count", "degree",
    ])
    write_csv(output / "query_table_references.csv", data["query_table_references"], [
        "query_id", "table", "reference_type", "resolution_status",
    ])
    write_csv(output / "unresolved_joins.csv", unresolved, [
        "query_id", "scope_id", "raw_predicate", "left_reference", "right_reference", "reason",
    ])
    write_csv(output / "failures.csv", failures, ["query_id", "error"])
    (output / "join_graph.dot").write_text(render_dot(data, 0), encoding="utf-8")
    (output / "join_graph.svg").write_text(render_svg(data, minimum), encoding="utf-8")
    template = Path(__file__).with_name("graph.html").read_text(encoding="utf-8")
    payload = json.dumps(data, sort_keys=True).replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    (output / "join_graph.html").write_text(
        template.replace("__GRAPH_DATA__", payload).replace("__MINIMUM__", str(minimum)), encoding="utf-8")
    top = sorted((e for e in data["edges"] if e["resolution_status"] == "exact"),
                 key=lambda e: (-e["query_count"], e["label"]))[:15]
    summary = [
        "# Generated workload join analysis", "",
        "Generated statically using SQLGlot; no SQL was executed.", "",
        "Open [join_graph.html](join_graph.html) for the offline interactive graph, or "
        "[join_graph.svg](join_graph.svg) for a static table-pair overview.", "",
        f"Selected queries: **{len(reports)}**. No flagged limitations: **{counts['success']}**; "
        f"partial: **{counts['partial']}**; failed: **{counts['failed']}**.",
        f"Physical tables: **{coverage['physical_tables']}**; physical column relationships: "
        f"**{coverage['physical_column_relationships']}**; physical table pairs: **{coverage['physical_table_pairs']}**.",
        f"Unresolved records: **{len(unresolved)}** (see [CSV](unresolved_joins.csv)).",
        "", "Frequency denominator is **all selected queries**, including failed queries. "
        "Counts measure syntax membership, not execution frequency or cost. Partial means at least one "
        "candidate could not be fully resolved; its other extracted edges remain useful. "
        "Success means no limitations were flagged by this adapter, not proof of complete SQL semantics.",
        "", "## Most frequent resolved column relationships", "",
        "| Relationship | Distinct queries | Occurrences |",
        "|---|---:|---:|",
    ]
    summary.extend(f"| `{e['label']}` | {e['query_count']} | {e['occurrence_count']} |" for e in top)
    summary.extend([
        "", "## Verification", "",
        "1. Find a relationship in [join_edges.csv](join_edges.csv); `query_ids` lists every supporting query.",
        "2. Follow its query links in the interactive graph, or use the `report` paths in "
        "[coverage_report.json](coverage_report.json). Each report retains original SQL, parser-normalized SQL, "
        "scope/alias roles and raw predicates.",
        "3. Check [unresolved_joins.csv](unresolved_joins.csv) before treating the physical graph as exhaustive. "
        "Non-physical CTE/derived endpoints are excluded from the default graph, not from the exports.",
        "4. Consult [manifest.json](manifest.json) for selected files, hashes, exclusions and per-query status.",
        "", "## Benchmarking next steps", "",
        "1. Match frequent fact-table columns against existing clustered primary keys and "
        "`scripts/indexing_strategy.sql`. A frequent join alone is not an index recommendation.",
        "2. Prioritize edges also used by the slow queries in `benchmark_results/*/summary.csv`; join "
        "`query_XXX.sql` evidence IDs to the profiler's `query_XXX` names.",
        "3. Examine selective WHERE predicates and actual execution plans for those queries. Check "
        "row estimates, scans/seeks, spills and logical reads. CTE/derived joins need manual review.",
        "4. Test a small candidate index set on a separate database; do not change the baseline. "
        "Compare against the existing nonclustered columnstore strategy as well as rowstore alternatives. "
        "Check key order, INCLUDE columns, storage, build cost and load overhead.",
        "5. Use `04_profile_queries.sh` on matching list entries for focused comparisons, then "
        "`03_run_benchmark.sh` for the unchanged full-workload warm-up plus five-run comparison. "
        "Keep scale factor, query files, statistics, cache protocol and server conditions fixed.",
        "", f"See [tool documentation]({Path(os.path.relpath(Path(__file__).with_name('README.md'), output)).as_posix()}) "
        "for setup, schemas, supported cases and limitations.", "",
    ])
    (output / "README.md").write_text("\n".join(summary), encoding="utf-8")
    return data
