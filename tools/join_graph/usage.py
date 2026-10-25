"""Workload usage analysis: filters, GROUP BY granularity, aggregated measures and hints.

Everything here is a syntax-level estimate over the selected query files. Counts are distinct
queries. Hints are starting points for experiments, not automatic recommendations.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INDEX_SCRIPT = ROOT / "scripts/indexing_strategy.sql"
DEFAULT_SCHEMA = ROOT / "tpcds-kit/tools/tpcds.sql"
DEFAULT_DATASET = ROOT / "data/1GB/dataset"
TINY_TABLE_ROWS = 1000
FILTER_HINT_MIN_QUERIES = 3

# Rank 0 is the finest level; a summary at rank r can answer queries needing rank >= r
# only when the coarser attributes are also kept in the summary.
_LEVELS: dict[str, dict[str, tuple[int, str]]] = {
    "date_dim": {
        **dict.fromkeys(("d_date_sk", "d_date_id", "d_date", "d_dom", "d_dow", "d_day_name",
                         "d_holiday", "d_weekend", "d_following_holiday"), (0, "day")),
        **dict.fromkeys(("d_week_seq", "d_fy_week_seq"), (1, "week")),
        **dict.fromkeys(("d_moy", "d_month_seq"), (2, "month")),
        **dict.fromkeys(("d_qoy", "d_quarter_seq", "d_quarter_name", "d_fy_quarter_seq"), (3, "quarter")),
        **dict.fromkeys(("d_year", "d_fy_year"), (4, "year")),
    },
    "time_dim": {
        **dict.fromkeys(("t_time_sk", "t_time_id", "t_time", "t_second"), (0, "second")),
        "t_minute": (1, "minute"), "t_hour": (2, "hour"),
        **dict.fromkeys(("t_am_pm", "t_shift", "t_sub_shift", "t_meal_time"), (3, "shift")),
    },
    "item": {
        **dict.fromkeys(("i_item_sk", "i_item_id", "i_item_desc", "i_product_name"), (0, "item")),
        **dict.fromkeys(("i_brand_id", "i_brand", "i_manufact_id", "i_manufact", "i_manager_id"),
                        (1, "brand/manufacturer")),
        **dict.fromkeys(("i_class_id", "i_class"), (2, "class")),
        **dict.fromkeys(("i_category_id", "i_category"), (3, "category")),
    },
    "store": {
        **dict.fromkeys(("s_store_sk", "s_store_id", "s_store_name", "s_street_number",
                         "s_street_name", "s_suite_number"), (0, "store")),
        **dict.fromkeys(("s_zip", "s_city", "s_county"), (1, "city/zip")),
        "s_state": (2, "state"),
        **dict.fromkeys(("s_company_id", "s_company_name", "s_country"), (3, "company/country")),
    },
    "customer_address": {
        **dict.fromkeys(("ca_address_sk", "ca_address_id", "ca_street_number", "ca_street_name",
                         "ca_street_type", "ca_suite_number"), (0, "address")),
        **dict.fromkeys(("ca_zip", "ca_city", "ca_county"), (1, "city/zip")),
        "ca_state": (2, "state"),
        **dict.fromkeys(("ca_country", "ca_gmt_offset"), (3, "country/time zone")),
    },
    "customer": dict.fromkeys(("c_customer_sk", "c_customer_id", "c_first_name", "c_last_name",
                               "c_salutation", "c_email_address", "c_login"), (0, "customer")),
    "warehouse": {
        **dict.fromkeys(("w_warehouse_sk", "w_warehouse_id", "w_warehouse_name"), (0, "warehouse")),
        **dict.fromkeys(("w_city", "w_county", "w_zip"), (1, "city/zip")),
        "w_state": (2, "state"), "w_country": (3, "country"),
    },
}


def short(table: str) -> str:
    return table.split(".")[-1]


def is_fact(table: str) -> bool:
    return short(table).endswith(("_sales", "_returns")) or short(table) == "inventory"


def level(table: str, columns: list[str] | tuple[str, ...]) -> str:
    """Friendly granularity label: finest known hierarchy level, else the column list."""
    known = [_LEVELS.get(short(table), {}).get(c) for c in columns]
    known = [k for k in known if k]
    if known:
        name = min(known)[1]
        extra = len(columns) - len(known)
        return name + (f" + {extra} other attribute{'s' * (extra > 1)}" if extra else "")
    return " + ".join(columns)


def count_rows(dataset: Path | None) -> dict[str, int]:
    """Row counts from dsdgen .dat files (one row per line); empty when unavailable."""
    if dataset is None or not dataset.is_dir():
        return {}
    counts = {}
    for path in sorted(dataset.glob("*.dat")):
        with path.open("rb") as file:
            counts[path.stem.lower()] = sum(chunk.count(b"\n") for chunk in iter(lambda: file.read(1 << 24), b""))
    return counts


def _columns(text: str) -> list[str]:
    return [re.sub(r"\s+(asc|desc)$", "", c.strip().strip("[]"), flags=re.I).lower()
            for c in text.split(",") if c.strip()]


def read_indexes(index_script: Path | None, schema: Path | None) -> list[dict[str, Any]]:
    """Primary keys from the schema DDL plus CREATE INDEX statements of the indexing script."""
    indexes = []
    if schema is not None and schema.is_file():
        for match in re.finditer(r"create\s+table\s+(\w+)\s*\((.*?)\n\);", schema.read_text(encoding="utf-8"),
                                 flags=re.I | re.S):
            key = re.search(r"primary\s+key\s*\(([^)]*)\)", match.group(2), flags=re.I)
            if key:
                indexes.append({"name": f"PK_{match.group(1).lower()}", "table": match.group(1).lower(),
                                "kind": "clustered primary key", "keys": _columns(key.group(1)),
                                "include": [], "source": "schema (both databases)"})
    if index_script is not None and index_script.is_file():
        pattern = (r"create\s+(?:unique\s+)?(?:(nonclustered|clustered)\s+)?(columnstore\s+)?index\s+(\w+)\s+"
                   r"on\s+(?:\w+\.)?(\w+)\s*\(([^)]*)\)(?:\s*include\s*\(([^)]*)\))?")
        for match in re.finditer(pattern, index_script.read_text(encoding="utf-8"), flags=re.I):
            columnstore = bool(match.group(2))
            kind = f"{(match.group(1) or 'nonclustered').lower()}{' columnstore' if columnstore else ''}"
            indexes.append({"name": match.group(3), "table": match.group(4).lower(), "kind": kind,
                            "keys": [] if columnstore else _columns(match.group(5)),
                            "include": _columns(match.group(5)) if columnstore else _columns(match.group(6) or ""),
                            "source": f"{index_script.name} (indexed database only)"})
    return indexes


def default_context() -> dict[str, Any]:
    return {"indexes": read_indexes(DEFAULT_INDEX_SCRIPT, DEFAULT_SCHEMA), "row_counts": count_rows(DEFAULT_DATASET),
            "index_script": str(DEFAULT_INDEX_SCRIPT), "dataset": str(DEFAULT_DATASET)}


def coverage(table: str, column: str, indexes: list[dict[str, Any]]) -> list[str]:
    found = []
    for index in indexes:
        if index["table"] != short(table):
            continue
        if column in index["keys"]:
            position = index["keys"].index(column) + 1
            found.append(f"{index['name']} key #{position}" + (" (leading)" if position == 1 else ""))
        elif column in index["include"]:
            found.append(f"{index['name']} ({'columnstore' if 'columnstore' in index['kind'] else 'INCLUDE'})")
    return found


def _per_query(report: dict[str, Any], name) -> dict[str, Any]:
    usage = report.get("usage") or {}
    key = lambda c: (name(c["table"]), c["column"])  # noqa: E731
    filters: dict[tuple[str, str], set[str]] = defaultdict(set)
    for item in usage.get("filters", []):
        if item["kind"] != "aggregate_value":
            for column in item["columns"]:
                filters[key(column)].add(item["kind"])
    aggregates: dict[tuple[str, str], set[str]] = defaultdict(set)
    for item in usage.get("aggregates", []):
        for column in item["columns"]:
            aggregates[key(column)].add(item["function"])
    joins = {(name(e["table"]), e["column"]) for j in report.get("joins", []) if j["resolution_status"] == "exact"
             for e in (j["left"], j["right"]) if e["column"]}
    groups = {tuple(sorted(key(c) for c in g["columns"])) for g in usage.get("group_by", []) if g["columns"]}
    referenced = {key(c) for item in usage.get("referenced", []) for c in item["columns"]} | joins
    tables = {name(r["table"]) for r in report.get("references", []) if r["reference_type"] == "physical"}
    return {"filters": filters, "aggregates": aggregates, "joins": joins, "groups": groups,
            "referenced": referenced, "tables": tables, "has_group_by": bool(usage.get("group_by")),
            "rollup": any(g.get("rollup") for g in usage.get("group_by", [])),
            "joined": [(name(j["left"]["table"]), name(j["right"]["table"])) for j in report.get("joins", [])
                       if j["resolution_status"] == "exact"]}


def signature(group: tuple[tuple[str, str], ...]) -> str:
    by_table: dict[str, list[str]] = defaultdict(list)
    for table, column in group:
        by_table[table].append(column)
    return " · ".join(f"{t}: {level(t, cols)}" for t, cols in sorted(by_table.items()))


def _ranked(counter: dict[Any, set[str]], limit: int | None = None) -> list[tuple[Any, list[str]]]:
    items = sorted(counter.items(), key=lambda kv: (-len(kv[1]), str(kv[0])))
    return [(k, sorted(v)) for k, v in items[:limit]]


def _levels(per_query: dict[str, dict], queries: list[str], table: str, source: str) -> list[dict[str, Any]]:
    found: dict[tuple[str, ...], set[str]] = defaultdict(set)
    for query_id in queries:
        q = per_query[query_id]
        if source == "group":
            sets = [tuple(sorted(c for t, c in g if t == table)) for g in q["groups"]]
        else:
            sets = [tuple(sorted(c for t, c in q["filters"] if t == table))]
        for columns in sets:
            if columns:
                found[columns].add(query_id)
    return [{"level": level(table, cols), "columns": list(cols), "query_count": len(ids), "query_ids": ids}
            for cols, ids in _ranked(found)]


def _level_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_level: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        by_level[row["level"]].update(row["query_ids"])
    return [{"level": k, "query_count": len(v), "query_ids": v} for k, v in _ranked(by_level)]


def analyze(reports: list[dict[str, Any]], data: dict[str, Any], context: dict[str, Any] | None) -> None:
    """Add usage, granularity and hint sections to aggregated graph data in place."""
    context = context or {"indexes": [], "row_counts": {}}
    indexes, rows = context["indexes"], context["row_counts"]
    # Plan graphs name tables dbo.x while SQL text usually says x; use the graph's node names.
    names = {short(n["table"]): n["table"] for n in data["nodes"] if n["reference_type"] == "physical"}
    name = lambda table: names.get(short(table), table)  # noqa: E731
    per_query = {r["query_id"]: _per_query(r, name) for r in reports if r["status"] != "failed"}
    columns: dict[tuple[str, str], dict[str, Any]] = {}

    def column(key: tuple[str, str]) -> dict[str, Any]:
        return columns.setdefault(key, {"filter": set(), "group": set(), "aggregate": set(), "join": set(),
                                        "kinds": defaultdict(set), "functions": defaultdict(set)})
    for query_id, q in per_query.items():
        for key, kinds in q["filters"].items():
            column(key)["filter"].add(query_id)
            for kind in kinds:
                column(key)["kinds"][kind].add(query_id)
        for key, functions in q["aggregates"].items():
            column(key)["aggregate"].add(query_id)
            for function in functions:
                column(key)["functions"][function].add(query_id)
        for group in q["groups"]:
            for key in group:
                column(key)["group"].add(query_id)
        for key in q["joins"]:
            column(key)["join"].add(query_id)
    usage_rows = []
    for (table, name), item in sorted(columns.items()):
        usage_rows.append({
            "table": table, "column": name, "fact_table": is_fact(table), "table_rows": rows.get(short(table)),
            "filter_query_count": len(item["filter"]), "group_by_query_count": len(item["group"]),
            "aggregate_query_count": len(item["aggregate"]), "join_query_count": len(item["join"]),
            "filter_kinds": {k: len(v) for k, v in sorted(item["kinds"].items())},
            "aggregate_functions": {k: len(v) for k, v in sorted(item["functions"].items())},
            "existing_indexes": coverage(table, name, indexes), "hierarchy_level": level(table, [name])
            if short(table) in _LEVELS and name in _LEVELS[short(table)] else "",
            "filter_query_ids": sorted(item["filter"]), "group_by_query_ids": sorted(item["group"]),
            "aggregate_query_ids": sorted(item["aggregate"]), "join_query_ids": sorted(item["join"]),
        })
    data["column_usage"] = usage_rows

    referencing: dict[str, list[str]] = defaultdict(list)
    for query_id, q in sorted(per_query.items()):
        for table in q["tables"]:
            referencing[table].append(query_id)
    table_usage = {}
    for table, queries in sorted(referencing.items()):
        group_levels = _levels(per_query, queries, table, "group")
        filter_levels = _levels(per_query, queries, table, "filter")
        grouped = {q for row in group_levels for q in row["query_ids"]}
        mine = [r for r in usage_rows if r["table"] == table]
        top = lambda field: [{"column": r["column"], "query_count": r[field]}  # noqa: E731
                             for r in sorted(mine, key=lambda r: (-r[field], r["column"])) if r[field]][:10]
        table_usage[table] = {
            "table": table, "fact_table": is_fact(table), "table_rows": rows.get(short(table)),
            "query_count": len(queries), "group_levels": group_levels[:12],
            "group_level_summary": _level_summary(group_levels),
            "filter_levels": filter_levels[:12], "filter_level_summary": _level_summary(filter_levels),
            "not_grouped_by_table_query_count": sum(per_query[q]["has_group_by"] for q in queries) - len(grouped),
            "no_group_by_query_count": sum(not per_query[q]["has_group_by"] for q in queries),
            "top_filter_columns": top("filter_query_count"), "top_group_by_columns": top("group_by_query_count"),
            "top_aggregated_columns": top("aggregate_query_count"), "top_join_columns": top("join_query_count"),
            "existing_indexes": [i for i in indexes if i["table"] == short(table)],
        }
    data["table_usage"] = table_usage

    for edge in data["edges"] + data["table_pairs"]:
        queries = [q for q in edge["query_ids"] if q in per_query]
        signatures: dict[str, set[str]] = defaultdict(set)
        for query_id in queries:
            for group in per_query[query_id]["groups"]:
                signatures[signature(group)].add(query_id)
        edge["grouping_levels"] = [{"signature": s, "query_count": len(ids), "query_ids": ids}
                                   for s, ids in _ranked(signatures, 10)]
        edge["no_group_by_query_count"] = sum(not per_query[q]["has_group_by"] for q in queries)
        edge["endpoint_group_levels"] = {
            table: [{k: row[k] for k in ("level", "query_count")} for row in
                    _level_summary(_levels(per_query, queries, table, "group"))]
            for table in sorted({edge["left_table"], edge["right_table"]}) if not table.startswith("@")
        }

    data["fact_grains"] = _fact_grains(per_query, usage_rows, rows)
    data["optimization_hints"] = _hints(per_query, usage_rows, data["fact_grains"], indexes, rows)
    data["usage_context"] = {
        "index_script": context.get("index_script"), "dataset": context.get("dataset"),
        "indexes_read": len(indexes), "tables_with_row_counts": len(rows),
        "tiny_table_rows": TINY_TABLE_ROWS,
    }


def _fact_grains(per_query: dict[str, dict], usage_rows: list[dict], rows: dict[str, int]) -> list[dict]:
    """For each fact, the dimension levels queries need (GROUP BY plus filter columns)."""
    facts = sorted({t for q in per_query.values() for t in q["tables"] if is_fact(t)})
    result = []
    for fact in facts:
        queries = sorted(q for q, info in per_query.items() if fact in info["tables"])
        dimension_levels: dict[tuple[str, str], set[str]] = defaultdict(set)
        signatures: dict[str, set[str]] = defaultdict(set)
        for query_id in queries:
            q = per_query[query_id]
            dimensions = {b if a == fact else a for a, b in q["joined"] if fact in (a, b)}
            dimensions = {d for d in dimensions if d != fact and not is_fact(d)}
            needed = {c for g in q["groups"] for c in g} | set(q["filters"])
            parts = []
            for dimension in sorted(dimensions):
                cols = sorted(c for t, c in needed if t == dimension)
                label = level(dimension, cols) if cols else "join only (no grouping/filter column)"
                dimension_levels[(dimension, label)].add(query_id)
                if cols:
                    parts.append(f"{dimension}: {level(dimension, cols)}")
            own = sorted(c for g in q["groups"] for t, c in g if t == fact)
            if own:
                parts.append(f"{fact}: {' + '.join(own)}")
            signatures[" · ".join(parts) or "no dimension grouping/filter"].add(query_id)
        measures = [r for r in usage_rows if r["table"] == fact and r["aggregate_query_count"]]
        result.append({
            "table": fact, "table_rows": rows.get(short(fact)), "query_count": len(queries), "query_ids": queries,
            "dimension_levels": [{"dimension": d, "level": lvl, "query_count": len(ids), "query_ids": ids}
                                 for (d, lvl), ids in _ranked(dimension_levels)],
            "top_signatures": [{"signature": s, "query_count": len(ids), "query_ids": ids}
                               for s, ids in _ranked(signatures, 10)],
            "measures": [{"column": r["column"], "query_count": r["aggregate_query_count"],
                          "functions": r["aggregate_functions"]}
                         for r in sorted(measures, key=lambda r: (-r["aggregate_query_count"], r["column"]))],
        })
    return sorted(result, key=lambda f: (-f["query_count"], f["table"]))


def _hints(per_query: dict[str, dict], usage_rows: list[dict], fact_grains: list[dict],
           indexes: list[dict], rows: dict[str, int]) -> list[dict[str, Any]]:
    hints = []
    tiny = sorted({r["table"] for r in usage_rows
                   if rows.get(short(r["table"])) is not None and rows[short(r["table"])] < TINY_TABLE_ROWS})
    candidates = [r for r in usage_rows if r["filter_query_count"] >= FILTER_HINT_MIN_QUERIES
                  and r["table"] not in tiny and not r["fact_table"]]
    for r in sorted(candidates, key=lambda r: (-r["filter_query_count"], r["table"], r["column"]))[:15]:
        leading = [i for i in r["existing_indexes"] if "(leading)" in i]
        status = ("already the leading key of " + ", ".join(leading) if leading else
                  "present in " + ", ".join(r["existing_indexes"]) + " but not as leading key"
                  if r["existing_indexes"] else "not in any known index")
        clustered = next((i["keys"] for i in indexes if i["table"] == short(r["table"])
                          and i["kind"] == "clustered primary key"), [])
        together: Counter[str] = Counter()
        for query_id in r["filter_query_ids"]:
            together.update(c for t, c in per_query[query_id]["filters"] if t == r["table"] and c != r["column"])
        co_filtered = ", ".join(f"{c} ({n})" for c, n in sorted(together.items(), key=lambda kv: (-kv[1], kv[0]))[:3])
        hints.append({
            "kind": "filter_index", "table": r["table"], "columns": [r["column"]],
            "title": f"Filter column {r['table']}.{r['column']}",
            "evidence": f"Filtered in {r['filter_query_count']} queries ({_kinds(r['filter_kinds'])}); "
                        f"table rows: {_rows(r['table_rows'])}; {status}.",
            "action": ("Leading key already exists: check that actual plans Seek on it before adding more."
                       if leading else f"Test CREATE INDEX IX_{short(r['table'])}_{r['column']} ON dbo.{short(r['table'])} "
                       f"({r['column']})" + (f"; the clustered key ({', '.join(clustered)}) is carried automatically "
                                              "for joins" if clustered else "") + ".")
            + (f" Often filtered together with {co_filtered}: consider a composite key." if co_filtered else ""),
            "caveat": "Helps only if the predicate is selective (returns a small fraction of rows). "
                      "Equality columns usually go before range columns in the key.",
            "query_ids": r["filter_query_ids"],
        })
    for fact in fact_grains:
        table = fact["table"]
        columnstores = [i for i in indexes if i["table"] == short(table) and "columnstore" in i["kind"]]
        queries = fact["query_ids"]
        if not queries:
            continue
        if columnstores:
            included = set(columnstores[0]["include"])
            missing: dict[str, set[str]] = defaultdict(set)
            covered = 0
            for query_id in queries:
                needed = {c for t, c in per_query[query_id]["referenced"] if t == table}
                if needed <= included:
                    covered += 1
                for c in needed - included:
                    missing[c].add(query_id)
            gaps = _ranked(missing, 8)
            evidence = (f"{columnstores[0]['name']} contains every referenced {table} column for "
                        f"{covered} of {len(queries)} queries.")
            action = ("Consider adding to the columnstore: " + ", ".join(f"{c} ({len(ids)} queries)" for c, ids in gaps)
                      if gaps else "Columnstore already covers all statically referenced columns.")
            query_ids = sorted({q for _, ids in gaps for q in ids})
        else:
            evidence = f"No columnstore index on {table} ({_rows(fact['table_rows'])} rows); referenced by {len(queries)} queries."
            action = "Candidate: nonclustered columnstore index on the join keys and aggregated measures listed below."
            query_ids = queries
        measures = ", ".join(f"{m['column']} ({m['query_count']})" for m in fact["measures"][:8])
        hints.append({
            "kind": "columnstore", "table": table, "columns": [m["column"] for m in fact["measures"]],
            "title": f"Columnstore coverage for {table}",
            "evidence": evidence + (f" Most aggregated measures: {measures}." if measures else ""),
            "action": action,
            "caveat": "If a query touches any column missing from a nonclustered columnstore, SQL Server "
                      "cannot answer it from that index alone and may scan the rowstore instead.",
            "query_ids": query_ids,
        })
        date_levels = [d for d in fact["dimension_levels"] if d["dimension"] == "date_dim"]
        grains = [g for g in fact["top_signatures"] if g["signature"] != "no dimension grouping/filter"]
        if date_levels and grains:
            top = grains[0]
            hints.append({
                "kind": "summary", "table": table, "columns": [],
                "title": f"Pre-aggregation levels for {table}",
                "evidence": "date_dim levels needed (GROUP BY + filters): " + ", ".join(
                    f"{d['level']} ({d['query_count']})" for d in date_levels[:5])
                    + f". Most common full grain: '{top['signature']}' in {top['query_count']} queries.",
                "action": "If many queries share a coarse grain (e.g. month or year), test an indexed view or "
                          "summary table at that grain and compare runtimes on those queries.",
                "caveat": "A summary must keep every GROUP BY and filter column the query needs. Indexed views "
                          "need SCHEMABINDING and COUNT_BIG(*), and may slow data loading.",
                "query_ids": top["query_ids"],
            })
    if tiny:
        hints.append({
            "kind": "skip", "table": "", "columns": [], "title": "Tables too small to benefit from extra indexes",
            "evidence": ", ".join(f"{t} ({_rows(rows.get(short(t)))} rows)" for t in tiny) + ".",
            "action": "Do not prioritize indexes here; scanning a few pages is already cheap.",
            "caveat": f"Threshold: fewer than {TINY_TABLE_ROWS} rows in the generated dataset.", "query_ids": [],
        })
    hints.append({
        "kind": "statistics", "table": "", "columns": [], "title": "Check estimates before adding indexes",
        "evidence": "Large row estimate errors in the actual-plan graph (rank by 'Worst row estimate error') "
                    "can cause bad join strategies regardless of indexes.",
        "action": "Run UPDATE STATISTICS ... WITH FULLSCAN (or create statistics on filtered columns) and re-measure "
                  "the worst queries before and after each index experiment.",
        "caveat": "Change one thing at a time; benchmark with 03_run_benchmark.sh on an unchanged workload.",
        "query_ids": [],
    })
    return hints


def _rows(value: int | None) -> str:
    return "unknown" if value is None else f"{value:,}"


def _kinds(kinds: dict[str, int]) -> str:
    return ", ".join(f"{k.replace('_', ' ')} ×{v}" for k, v in sorted(kinds.items(), key=lambda kv: -kv[1]))
