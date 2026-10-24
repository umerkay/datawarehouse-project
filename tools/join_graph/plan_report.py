"""Extract join evidence and estimation accuracy from SQL Server Showplan XML."""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from statistics import mean
from typing import Any

from report import canonical, export, write_csv, write_json

LOGGER = logging.getLogger("join_graph.plans")
NS = {"p": "http://schemas.microsoft.com/sqlserver/2004/07/showplan"}
URI = NS["p"]
OPERATORS = {"EQ": "=", "NE": "<>", "GT": ">", "GE": ">=", "LT": "<", "LE": "<="}


def split_documents(stdout: str) -> list[str]:
    # ODBC sqlcmd hard-wraps nvarchar(max) at -w, even inside XML tag names.
    # SHOWPLAN emits one serialized line (SQL newlines are XML character entities).
    stdout = stdout.replace("\r", "").replace("\n", "")
    documents = re.findall(r"<ShowPlanXML\b.*?</ShowPlanXML>", stdout, flags=re.S)
    if not documents:
        raise ValueError("sqlcmd returned no complete ShowPlanXML document (possibly truncated)")
    for document in documents:
        try:
            root = ET.fromstring(document)
        except ET.ParseError as error:
            raise ValueError(f"Invalid / truncated Showplan XML: {error}") from error
        if root.tag != f"{{{URI}}}ShowPlanXML":
            raise ValueError("Unexpected Showplan namespace")
    if stdout.count("<ShowPlanXML") != len(documents):
        raise ValueError("Incomplete Showplan document in sqlcmd output")
    return documents


def name(value: str) -> str:
    return value[1:-1].replace("]]", "]").lower() if value.startswith("[") and value.endswith("]") else value.lower()


def endpoint(column: ET.Element | None) -> dict[str, str] | None:
    if column is None or not column.get("Table") or not column.get("Column"):
        return None
    table = name(column.attrib["Table"])
    if column.get("Schema"):
        table = name(column.attrib["Schema"]) + "." + table
    return {"table": table, "column": name(column.attrib["Column"]),
            "role": name(column.get("Alias", column.attrib["Table"])), "reference_type": "physical"}


def numeric(element: ET.Element, attribute: str) -> float | None:
    raw = element.get(attribute)
    if raw is None:
        return None
    value = float(raw)
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"Invalid Showplan {attribute}: {raw}")
    return value


def local_elements(root: ET.Element):
    """Walk an operator's own properties but do not descend into child RelOps."""
    yield root
    for child in root:
        if child.tag != f"{{{URI}}}RelOp":
            yield from local_elements(child)


def access_objects(relop: ET.Element) -> list[dict[str, str]]:
    objects = {}
    for obj in relop.findall(".//p:Object", NS):
        if not obj.get("Table"):
            continue
        table = ".".join(name(obj.get(k, "")) for k in ("Schema", "Table") if obj.get(k))
        item = {"table": table, "alias": name(obj.get("Alias", obj.get("Table", ""))),
                "index": name(obj.get("Index", "")), "storage": obj.get("Storage", "")}
        objects[json.dumps(item, sort_keys=True)] = item
    return [objects[key] for key in sorted(objects)]


def row_deviation(actual: float | None, estimated: float | None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "actual_to_estimated_ratio": None, "row_deviation_pct": None,
        "row_error_factor": None, "row_error_unbounded": False, "row_deviation_direction": "unavailable",
    }
    if actual is None or estimated is None:
        return result
    result["row_deviation_direction"] = (
        "underestimated" if actual > estimated else "overestimated" if actual < estimated else "equal")
    if estimated > 0:
        result["actual_to_estimated_ratio"] = actual / estimated
        result["row_deviation_pct"] = (actual - estimated) / estimated * 100
    if actual == estimated == 0:
        result.update(actual_to_estimated_ratio=1, row_deviation_pct=0, row_error_factor=1)
    elif min(actual, estimated) == 0:
        result["row_error_unbounded"] = True
    else:
        result["row_error_factor"] = max(actual / estimated, estimated / actual)
    return result


def runtime_metrics(relop: ET.Element) -> dict[str, Any]:
    counters = relop.findall("./p:RunTimeInformation/p:RunTimeCountersPerThread", NS)
    executions = [numeric(c, "ActualExecutions") for c in counters]
    rows = [numeric(c, "ActualRows") for c in counters]
    # Parallel workers are parts of the same invocation, not separate serial executions.
    complete = bool(counters) and all(v is not None for v in executions + rows)
    count = max(executions) if complete else None
    total = sum(rows) if complete else None
    per_execution = total / count if count else None
    elapsed = [numeric(c, "ActualElapsedms") for c in counters]
    cpu = [numeric(c, "ActualCPUms") for c in counters]
    state = "unavailable" if not complete else "not_executed" if count == 0 else "executed"
    return {
        "runtime_state": state,
        "actual_rows_total": total, "actual_executions": count,
        "actual_rows_per_execution": per_execution,
        "actual_elapsed_ms": max(elapsed) if elapsed and all(v is not None for v in elapsed) else None,
        "actual_cpu_ms": sum(cpu) if cpu and all(v is not None for v in cpu) else None,
        "runtime_counters": [dict(c.attrib) for c in counters],
        **row_deviation(per_execution, numeric(relop, "EstimateRows")),
    }


def parse_plan(document: str, query_id: str, document_index: int = 0,
               actual: bool = False) -> dict[str, Any]:
    root = ET.fromstring(document)
    if root.tag != f"{{{URI}}}ShowPlanXML":
        raise ValueError("Unexpected Showplan namespace")
    has_runtime = root.find(".//p:RunTimeInformation", NS) is not None
    if has_runtime and not actual:
        raise ValueError("Actual execution plan supplied; this report accepts estimates only")
    if actual and not has_runtime:
        raise ValueError("Actual plan requested but runtime counters are missing")
    statements = root.findall(".//p:StmtSimple", NS)
    if not statements:
        raise ValueError("No statement plan found in Showplan XML")
    report: dict[str, Any] = {
        "query_id": query_id, "status": "success", "references": [], "joins": [],
        "unresolved": [], "join_operators": [], "statements": [],
    }
    refs = {}
    for statement_index, stmt in enumerate(statements):
        statement_id = f"d{document_index}.s{statement_index}"
        timing = stmt.find("./p:QueryPlan/p:QueryTimeStats", NS)
        report["statements"].append({
            "statement_id": statement_id, "statement_text": stmt.get("StatementText"),
            "statement_estimated_rows": numeric(stmt, "StatementEstRows"),
            "estimated_subtree_cost": numeric(stmt, "StatementSubTreeCost"),
            "optimization_level": stmt.get("StatementOptmLevel"),
            "optimization_early_abort_reason": stmt.get("StatementOptmEarlyAbortReason"),
            "cardinality_estimation_model": stmt.get("CardinalityEstimationModelVersion"),
            "set_options": stmt.find("p:StatementSetOptions", NS).attrib
            if stmt.find("p:StatementSetOptions", NS) is not None else {},
            "actual_elapsed_ms": numeric(timing, "ElapsedTime") if timing is not None else None,
            "actual_cpu_ms": numeric(timing, "CpuTime") if timing is not None else None,
        })
        for relop in stmt.findall(".//p:RelOp", NS):
            for obj in access_objects(relop):
                refs[obj["table"]] = {
                    "table": obj["table"], "role": obj["alias"],
                    "reference_type": "physical", "resolution_status": "exact",
                }
            physical = relop.get("PhysicalOp", "")
            logical = relop.get("LogicalOp", "")
            if physical not in ("Hash Match", "Merge Join", "Nested Loops", "Adaptive Join"):
                continue
            if "Join" not in logical and logical != "Cross Apply":
                continue
            node_id = relop.get("NodeId", "")
            operator_id = f"{query_id}:{statement_id}:n{node_id}"
            children = relop.findall("./*/p:RelOp", NS)
            properties = next((c for c in relop if c.tag.rsplit("}", 1)[-1] in
                               ("Hash", "Merge", "NestedLoops", "AdaptiveJoin")), None)
            if properties is None:
                raise ValueError(f"Join operator {operator_id} lacks recognized operator properties")
            own = list(local_elements(properties))
            predicates = [e.get("ScalarString") for e in own if e.get("ScalarString")]
            operator: dict[str, Any] = {
                "operator_id": operator_id, "query_id": query_id, "statement_id": statement_id,
                "node_id": node_id, "physical_operator": physical, "logical_operator": logical,
                "estimated_rows": numeric(relop, "EstimateRows"),
                "estimated_subtree_cost": numeric(relop, "EstimatedTotalSubtreeCost"),
                "estimated_rebinds": numeric(relop, "EstimateRebinds"),
                "estimated_rewinds": numeric(relop, "EstimateRewinds"),
                "execution_mode": relop.get("EstimatedExecutionMode"),
                "parallel": relop.get("Parallel"),
                "estimated_join_type": relop.get("EstimatedJoinType"),
                "adaptive_threshold_rows": numeric(relop, "AdaptiveThresholdRows"),
                "inputs": [{"node_id": child.get("NodeId"), "estimated_rows": numeric(child, "EstimateRows"),
                            "tables_and_indexes": access_objects(child)} for child in children],
                "predicates": sorted(set(predicates)), "mapped_relationships": [],
                "warnings_xml": [ET.tostring(e, encoding="unicode") for e in own
                                 if e.tag == f"{{{URI}}}Warnings"],
            }
            if actual:
                operator.update(runtime_metrics(relop))
            candidates: list[tuple[dict, dict, str, str]] = []

            def add_columns(left: ET.Element | None, right: ET.Element | None, op: str, signal: str):
                l, r = endpoint(left), endpoint(right)
                if l is not None and r is not None and (
                    l["table"], l["role"]) != (r["table"], r["role"]):
                    candidates.append((l, r, op, signal))

            for left_name, right_name in (("HashKeysBuild", "HashKeysProbe"),
                                           ("OuterSideJoinColumns", "InnerSideJoinColumns")):
                lefts = properties.findall(f"./p:{left_name}/p:ColumnReference", NS)
                rights = properties.findall(f"./p:{right_name}/p:ColumnReference", NS)
                if len(lefts) == len(rights):
                    for left, right in zip(lefts, rights):
                        add_columns(left, right, "=", f"{left_name}/{right_name}")
            for element in own:
                if element.tag != f"{{{URI}}}Compare" or element.get("CompareOp") not in OPERATORS:
                    continue
                operands = element.findall("./p:ScalarOperator", NS)
                if len(operands) == 2:
                    add_columns(operands[0].find("./p:Identifier/p:ColumnReference", NS),
                                operands[1].find("./p:Identifier/p:ColumnReference", NS),
                                OPERATORS[element.attrib["CompareOp"]], "operator predicate")
            # Nested-loop joins often express the relationship only as a parameterized
            # seek in the inner input, not as a predicate on the join operator itself.
            if physical == "Nested Loops" and len(children) == 2:
                outer_refs = {(endpoint(e) or {}).get("role", "") + ":" + (endpoint(e) or {}).get("column", "")
                              for e in properties.findall("./p:OuterReferences/p:ColumnReference", NS)
                              if endpoint(e) is not None}
                for seek_relop in children[1].iter(f"{{{URI}}}RelOp"):
                    for section in local_elements(seek_relop):
                        if section.tag.rsplit("}", 1)[-1] not in ("Prefix", "StartRange", "EndRange"):
                            continue
                        columns = section.findall("./p:RangeColumns/p:ColumnReference", NS)
                        values = section.findall("./p:RangeExpressions/p:ScalarOperator", NS)
                        op = OPERATORS.get(section.get("ScanType", ""))
                        if len(columns) == len(values) and op:
                            for column, value in zip(columns, values):
                                other = value.find("./p:Identifier/p:ColumnReference", NS)
                                resolved = endpoint(other)
                                if resolved and f"{resolved['role']}:{resolved['column']}" in outer_refs:
                                    add_columns(column, other, op, "parameterized inner seek")
            seen = set()
            for left, right, op, signal in candidates:
                join = {
                    "query_id": query_id, "scope_id": statement_id, "operator_id": operator_id,
                    "left": left, "right": right, "operator": op,
                    "relationship_class": "equality" if op == "=" else "non_equality",
                    "join_type": logical, "resolution_status": "exact",
                    "self_join": left["table"] == right["table"], "conditional": False,
                    "raw_predicate": f"{left['table']}.{left['column']} {op} {right['table']}.{right['column']}",
                    "evidence_signal": signal,
                }
                key = canonical(join)
                if key in seen:
                    continue
                seen.add(key)
                operator["mapped_relationships"].append(list(key))
                report["joins"].append(join)
            if not seen:
                report["unresolved"].append({
                    "query_id": query_id, "scope_id": statement_id,
                    "operator_id": operator_id, "raw_predicate": "; ".join(operator["predicates"]),
                    "left_reference": json.dumps(operator["inputs"][0] if children else {}),
                    "right_reference": json.dumps(operator["inputs"][1] if len(children) > 1 else {}),
                    "reason": "Join between intermediate/complex inputs; no explicit physical-column pair resolved",
                })
            report["join_operators"].append(operator)
    report["references"] = [refs[key] for key in sorted(refs)]
    if report["unresolved"]:
        report["status"] = "partial"
    return report


def summarize(operators: list[dict[str, Any]]) -> dict[str, Any]:
    unique = {op["operator_id"]: op for op in operators}
    values = [op["estimated_rows"] for op in unique.values() if op["estimated_rows"] is not None]
    result = {
        "operator_count": len(unique), "operators_with_row_estimates": len(values),
        "estimated_rows_min": min(values) if values else None,
        "estimated_rows_max": max(values) if values else None,
        "estimated_rows_avg": mean(values) if values else None,
        "physical_operator_summary": dict(sorted(Counter(op["physical_operator"] for op in unique.values()).items())),
        "logical_operator_summary": dict(sorted(Counter(op["logical_operator"] for op in unique.values()).items())),
        "operator_ids": sorted(unique),
    }
    if any("runtime_state" in op for op in unique.values()):
        executed = [op for op in unique.values() if op.get("runtime_state") == "executed"]
        measured = [op["actual_rows_per_execution"] for op in executed]
        factors = [op["row_error_factor"] for op in executed if op["row_error_factor"] is not None]
        result.update({
            "executed_operator_count": len(executed),
            "not_executed_operator_count": sum(op.get("runtime_state") == "not_executed" for op in unique.values()),
            "actual_rows_per_execution_min": min(measured) if measured else None,
            "actual_rows_per_execution_max": max(measured) if measured else None,
            "actual_rows_per_execution_avg": mean(measured) if measured else None,
            "row_error_factor_max": max(factors) if factors else None,
            "row_error_unbounded_count": sum(op["row_error_unbounded"] for op in executed),
            "underestimated_operator_count": sum(op["row_deviation_direction"] == "underestimated" for op in executed),
            "overestimated_operator_count": sum(op["row_deviation_direction"] == "overestimated" for op in executed),
        })
    return result


def query_metrics(report: dict[str, Any]) -> dict[str, Any]:
    statements = report["statements"]
    elapsed = [s["actual_elapsed_ms"] for s in statements]
    cpu = [s["actual_cpu_ms"] for s in statements]
    measured = [op for op in report["join_operators"] if op.get("runtime_state") == "executed"]
    worst = max(measured, key=lambda op: (op["row_error_unbounded"],
                                          op["row_error_factor"] or 0, op["operator_id"])) if measured else None
    return {
        "query_id": report["query_id"], "status": report["status"],
        "failure_kind": report.get("failure_kind"), "error": report.get("error"),
        "estimated_cost": statements[0]["estimated_subtree_cost"] if len(statements) == 1 else None,
        "actual_elapsed_ms": sum(elapsed) if elapsed and all(v is not None for v in elapsed) else None,
        "actual_cpu_ms": sum(cpu) if cpu and all(v is not None for v in cpu) else None,
        "client_elapsed_ms": report.get("client_elapsed_ms"),
        "executed_join_operator_count": len(measured),
        "worst_operator_id": worst["operator_id"] if worst else None,
        "worst_row_error_factor": worst["row_error_factor"] if worst else None,
        "worst_row_error_unbounded": worst["row_error_unbounded"] if worst else False,
        "worst_row_deviation_pct": worst["row_deviation_pct"] if worst else None,
        "worst_row_deviation_direction": worst["row_deviation_direction"] if worst else None,
    }


def generate(output: Path) -> dict[str, Any]:
    manifest = json.loads((output / "plan_manifest.json").read_text(encoding="utf-8"))
    actual = manifest["mode"] == "actual_STATISTICS_XML"
    reports = []
    operators = []
    for entry in manifest["queries"]:
        report: dict[str, Any] = {
            "query_id": entry["query_id"], "status": "failed", "original_sql": entry.get("original_sql"),
            "source_sha256": entry.get("source_sha256"), "references": [], "joins": [], "unresolved": [],
            "join_operators": [], "statements": [], "plan_files": entry["plan_files"],
            "client_elapsed_ms": entry.get("client_elapsed_ms"),
            "failure_kind": entry.get("failure_kind"),
        }
        try:
            if entry["status"] != "collected":
                if not report["failure_kind"] and entry.get("stderr"):
                    diagnostic = (output / entry["stderr"]).read_text(encoding="utf-8")
                    if "timeout" in diagnostic.lower():
                        report["failure_kind"] = "timeout"
                        raise ValueError(f"Timeout: {diagnostic.strip()}")
                raise ValueError(entry.get("error", "Plan collection failed"))
            if not entry["plan_files"]:
                raise ValueError("No XML plan files in collected entry")
            for i, path in enumerate(entry["plan_files"]):
                parsed = parse_plan((output / path).read_text(encoding="utf-8"), entry["query_id"], i, actual)
                for key in ("references", "joins", "unresolved", "join_operators", "statements"):
                    report[key].extend(parsed[key])
            report["status"] = "partial" if report["unresolved"] else "success"
        except (ValueError, OSError, ET.ParseError) as error:
            report.update(status="failed", error=str(error), references=[], joins=[], unresolved=[],
                          join_operators=[], statements=[])
            LOGGER.error("%s: %s", entry["query_id"], error)
        reports.append(report)
        operators.extend(report["join_operators"])
    selection = dict(manifest["selection"])
    selection["queries"] = [{"query_id": r["query_id"], "status": r["status"]} for r in reports]
    data = export(output, reports, selection, 5, {
        "plan_database": manifest["database"], "collection_mode": manifest["mode"],
        "collected_at_utc": manifest["collected_at_utc"],
    })
    op_by_id = {op["operator_id"]: op for op in operators}
    edge_operators: dict[tuple, set[str]] = {}
    for report in reports:
        for join in report["joins"]:
            edge_operators.setdefault(canonical(join), set()).add(join["operator_id"])
    for edge in data["edges"]:
        key = tuple(edge[k] for k in ("left_table", "left_column", "right_table", "right_column",
                                      "relationship_class", "operator"))
        edge["plan_metrics"] = summarize([op_by_id[oid] for oid in sorted(edge_operators[key])])
    edge_by_id = {edge["edge_id"]: edge for edge in data["edges"]}
    for pair in data["table_pairs"]:
        ids = {oid for eid in pair["edge_ids"] for oid in edge_by_id[eid]["plan_metrics"]["operator_ids"]}
        pair["plan_metrics"] = summarize([op_by_id[oid] for oid in sorted(ids)])
    data["plan_evidence"] = {
        "database": manifest["database"], "mode": "actual" if actual else "estimated_only",
        "collected_at_utc": manifest["collected_at_utc"],
        "plan_files": {r["query_id"]: r["plan_files"] for r in reports},
        "all_operators": summarize(operators),
        "unmapped_operator_count": sum(not op["mapped_relationships"] for op in operators),
    }
    data["coverage"]["join_operator_count"] = len(operators)
    data["coverage"]["mapped_join_operator_count"] = sum(bool(op["mapped_relationships"]) for op in operators)
    data["coverage"]["unmapped_join_operator_count"] = data["plan_evidence"]["unmapped_operator_count"]
    early_aborts = [{"query_id": r["query_id"], **statement} for r in reports for statement in r["statements"]
                    if statement["optimization_early_abort_reason"]]
    data["coverage"]["optimizer_early_abort_count"] = len(early_aborts)
    data["coverage"]["optimizer_early_abort_reasons"] = dict(sorted(Counter(
        s["optimization_early_abort_reason"] for s in early_aborts).items()))
    data["coverage"]["timed_out_queries"] = sum(r.get("failure_kind") == "timeout" for r in reports)
    data["query_metrics"] = [query_metrics(r) for r in reports]
    write_json(output / "coverage_report.json", data["coverage"])
    write_json(output / "aggregate.json", data)
    write_csv(output / "join_operators.csv", operators, [
        "operator_id", "query_id", "statement_id", "node_id", "physical_operator", "logical_operator",
        "estimated_rows", "estimated_subtree_cost", "estimated_rebinds", "estimated_rewinds",
        "execution_mode", "parallel", "estimated_join_type", "adaptive_threshold_rows",
        "inputs", "predicates", "mapped_relationships", "warnings_xml",
        "runtime_state", "actual_rows_total", "actual_executions", "actual_rows_per_execution",
        "actual_elapsed_ms", "actual_cpu_ms", "actual_to_estimated_ratio", "row_deviation_pct",
        "row_error_factor", "row_error_unbounded", "row_deviation_direction", "runtime_counters",
    ])
    write_csv(output / "query_metrics.csv", data["query_metrics"], [
        "query_id", "status", "failure_kind", "estimated_cost", "actual_elapsed_ms", "actual_cpu_ms",
        "client_elapsed_ms", "executed_join_operator_count", "worst_operator_id", "worst_row_error_factor",
        "worst_row_error_unbounded", "worst_row_deviation_pct", "worst_row_deviation_direction", "error",
    ])
    write_csv(output / "optimizer_early_aborts.csv", early_aborts, [
        "query_id", "statement_id", "optimization_early_abort_reason", "optimization_level",
        "estimated_subtree_cost", "cardinality_estimation_model",
    ])
    for filename, rows in (("plan_edge_metrics.csv", data["edges"]), ("plan_pair_metrics.csv", data["table_pairs"])):
        flat = [{**row, **row["plan_metrics"]} for row in rows]
        write_csv(output / filename, flat, [
            "edge_id", "left_table", "left_column", "right_table", "right_column", "query_count",
            "occurrence_count", "operator_count", "operators_with_row_estimates", "estimated_rows_min",
            "estimated_rows_max", "estimated_rows_avg", "physical_operator_summary", "logical_operator_summary",
            "operator_ids",
            "executed_operator_count", "not_executed_operator_count", "actual_rows_per_execution_min",
            "actual_rows_per_execution_max", "actual_rows_per_execution_avg", "row_error_factor_max",
            "row_error_unbounded_count", "underestimated_operator_count", "overestimated_operator_count",
        ])
    template = Path(__file__).with_name("graph.html").read_text(encoding="utf-8")
    payload = json.dumps(data, sort_keys=True).replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    (output / "join_graph.html").write_text(template.replace("__GRAPH_DATA__", payload).replace("__MINIMUM__", "5"),
                                          encoding="utf-8")
    coverage = data["coverage"]
    summary = [
        f"# {'Actual' if actual else 'Estimated'} plan evidence: {manifest['database']}", "",
        f"Collected at {manifest['collected_at_utc']}. " +
        ("**SELECTs were executed once with SET STATISTICS XML ON**, with a per-query timeout. "
         "This diagnostic run is not the project's repeated benchmark protocol." if actual else
         "Using **SET SHOWPLAN_XML ON**: SELECTs were compiled, not executed; there are no actual row counts or timings."),
        "",
        "[Interactive plan graph](join_graph.html) | [Operator CSV](join_operators.csv) | "
        "[Column metrics](plan_edge_metrics.csv) | [Table-pair metrics](plan_pair_metrics.csv) | "
        "[Coverage](coverage_report.json)", "",
        f"Selected: **{coverage['discovered_queries']}**; parsed plans: **{coverage['parsed_queries']}**; "
        f"failed: **{coverage['failed_queries']}**; physical join operators: **{len(operators)}**; "
        f"operators with mapped physical predicates: **{coverage['mapped_join_operator_count']}**; "
        f"unmapped: **{coverage['unmapped_join_operator_count']}**.", "",
        f"Timed-out queries: **{coverage['timed_out_queries']}**. "
        "Failures are retained in the denominator and have no fabricated runtime rows or timings. "
        "See [query_metrics.csv](query_metrics.csv).", "",
        f"Optimizer early-abort reasons: **{json.dumps(coverage['optimizer_early_abort_reasons'])}**. "
        "These describe search termination inside the optimizer, not client timeout or query failure. "
        "See [optimizer_early_aborts.csv](optimizer_early_aborts.csv).", "",
        "## What the numbers mean", "",
        "- The edge is supported by explicit physical-column evidence in a hash/merge key, direct join "
        "predicate or parameterized nested-loop inner seek. No input-subtree Cartesian expansion is used.",
        "- Min/max/avg are **estimated output rows of associated join operators**, over unique "
        "(query file, statement, NodeId) observations. A multi-key join contributes once to table-pair metrics. "
        "The same operator estimate can appear on several attribute edges. These are not table-to-table "
        "selectivities, actual rows, runtime samples, or confidence intervals.",
        "- EstimateRows is preserved as written in XML, not multiplied by executions/rebinds or parallel "
        "threads. Inner input estimates may be per invocation. Subtree costs are optimizer units, not seconds.",
        "- Adaptive Join is a possible runtime choice, not proof of the strategy that would execute. "
        "EstimatedJoinType and AdaptiveThresholdRows are retained per operator. Alternative plan branches "
        "can both appear in static operator counts; these counts are not execution counts.",
        "- Unmapped joins between computed/aggregate/intermediate inputs remain in join_operators.csv and "
        "per-query reports. Even a mapped operator may have other unresolved predicate components; mapping "
        "does not prove complete predicate/lineage extraction.",
        "- Optimizer elimination, decorrelation and rewrites make these counts different from syntax counts. "
        "Keep the original static graph as the workload-topology reference.",
        "", "## Verify and compare", "",
        "1. Click an edge, inspect its strategies and row estimates, then open the linked query report "
        "and raw .sqlplan XML. Operator IDs identify exactly which estimates were aggregated.",
        "2. Check all operators, including unmapped ones, before choosing indexes. The manifest contains "
        "SQL hashes, server/database settings, indexes and statistics dates from before collection.",
        "3. Compare baseline versus indexed estimates only for the same SQL hashes. Plan changes are "
        "hypotheses to test with actual plans, logical reads and the project's existing benchmark protocol.",
        "4. Compilation can consume CPU, acquire schema locks and trigger automatic statistics work. "
        "Collect outside measured benchmark runs. Per-query sqlcmd and container-side timeouts bound collection.",
        "", f"See [tool documentation]({Path(os.path.relpath(Path(__file__).with_name('README.md'), output)).as_posix()}) "
        "for collection commands and limitations.", "",
    ]
    if actual:
        summary.extend([
            "## Runtime measurements and estimator deviation", "",
            "Query elapsed/CPU milliseconds come from QueryTimeStats, not optimizer cost. "
            "Client elapsed includes Docker/sqlcmd, compilation and result transfer; timeouts only have client time. "
            "Optimizer cost has no actual counterpart in seconds; no cost-to-time percentage is computed.", "",
            "Actual rows are summed across threads, then divided by the maximum ActualExecutions "
            "among workers to compare output per invocation with EstimateRows. Zero-execution adaptive "
            "branches are excluded. Raw counters are retained for inspection; unusual worker imbalance or "
            "invocation patterns require manual review.", "",
            "Signed deviation = 100 × (actual per execution − estimated) / estimated. "
            "Error factor = max(actual/estimated, estimated/actual). One zero versus a positive value "
            "is flagged unbounded, not replaced by a made-up denominator. Both zero means factor 1. "
            "Worst query errors include unmapped join operators, not just visible edges.", "",
            "| Query | Status | Server elapsed ms | Worst join row error |",
            "|---|---|---:|---|",
        ])
        ranked = sorted(data["query_metrics"], key=lambda q: (
            not q["worst_row_error_unbounded"], -(q["worst_row_error_factor"] or 0), q["query_id"]))
        for q in ranked[:15]:
            error = ("unbounded" if q["worst_row_error_unbounded"] else
                     f"{q['worst_row_error_factor']:.2f}x" if q["worst_row_error_factor"] is not None else "unavailable")
            summary.append(f"| {q['query_id']} | {q['status']} | "
                           f"{q['actual_elapsed_ms'] if q['actual_elapsed_ms'] is not None else 'unavailable'} | {error} |")
    (output / "README.md").write_text("\n".join(summary), encoding="utf-8")
    return data


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rebuild reports from already collected estimated plans (offline)")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = generate(args.output)
    print(json.dumps({key: result["coverage"][key] for key in (
        "parsed_queries", "failed_queries", "join_operator_count", "mapped_join_operator_count",
        "unmapped_join_operator_count", "optimizer_early_abort_reasons",
    )}, indent=2))
