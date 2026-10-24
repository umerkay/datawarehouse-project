from __future__ import annotations

import copy
import csv
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))

from analyze_workload import run
from extractor import extract, read_schema
from report import aggregate, canonical, render_dot, report_filename


def fixture_join(query_id="a.sql", left=("a", "id"), right=("b", "a_id")):
    return {
        "query_id": query_id, "scope_id": "s0",
        "left": {"table": left[0], "column": left[1], "role": "s0:a", "reference_type": "physical"},
        "right": {"table": right[0], "column": right[1], "role": "s0:b", "reference_type": "physical"},
        "operator": "=", "relationship_class": "equality", "join_type": "INNER",
        "resolution_status": "exact", "self_join": left[0] == right[0],
        "conditional": False, "raw_predicate": "a.id = b.a_id",
    }


def fixture_report(query_id, joins, failed=False):
    tables = sorted({j[side]["table"] for j in joins for side in ("left", "right")})
    return {
        "query_id": query_id, "status": "failed" if failed else "success",
        "references": [{"table": t, "reference_type": "physical", "resolution_status": "exact"} for t in tables],
        "joins": joins, "unresolved": [], **({"error": "fixture failure"} if failed else {}),
    }


class AggregationTests(unittest.TestCase):
    def test_repeated_across_queries_and_reversal(self):
        first = fixture_join()
        second = fixture_join("b.sql", ("b", "a_id"), ("a", "id"))
        data = aggregate([fixture_report("a.sql", [first]), fixture_report("b.sql", [second])])
        self.assertEqual(len(data["edges"]), 1)
        self.assertEqual(data["edges"][0]["query_count"], 2)
        self.assertEqual(data["edges"][0]["occurrence_count"], 2)
        self.assertEqual(canonical(first), canonical(second))

    def test_multiple_columns_and_pair_union_not_sum(self):
        first = fixture_join()
        second = fixture_join(left=("a", "code"), right=("b", "code"))
        data = aggregate([fixture_report("a.sql", [first, second])])
        self.assertEqual(len(data["edges"]), 2)
        self.assertEqual(len(data["table_pairs"]), 1)
        pair = data["table_pairs"][0]
        self.assertEqual((pair["query_count"], pair["occurrence_count"], pair["relationship_count"]), (1, 2, 2))

    def test_repeated_in_query_and_node_counts(self):
        data = aggregate([fixture_report("a.sql", [fixture_join(), fixture_join()])])
        edge = data["edges"][0]
        self.assertEqual((edge["query_count"], edge["occurrence_count"]), (1, 2))
        node = data["nodes"][0]
        self.assertEqual((node["query_count"], node["join_query_count"], node["join_occurrence_count"]), (1, 1, 2))

    def test_failed_queries_are_in_frequency_denominator(self):
        data = aggregate([fixture_report("a.sql", [fixture_join()]), fixture_report("b.sql", [], True)])
        self.assertEqual(data["edges"][0]["query_frequency"], .5)

    def test_order_independent(self):
        reports = [fixture_report("a.sql", [fixture_join()]),
                   fixture_report("b.sql", [fixture_join("b.sql", ("b", "a_id"), ("a", "id"))])]
        self.assertEqual(aggregate(reports), aggregate(list(reversed(reports))))
        self.assertEqual(aggregate(reports), aggregate(copy.deepcopy(reports)))

    def test_inequality_reverses_operator(self):
        first = fixture_join()
        first.update(operator="<", relationship_class="non_equality")
        second = fixture_join(left=("b", "a_id"), right=("a", "id"))
        second.update(operator=">", relationship_class="non_equality")
        self.assertEqual(canonical(first), canonical(second))

    def test_join_type_separate_from_edge_identity(self):
        second = fixture_join()
        second["join_type"] = "LEFT"
        data = aggregate([fixture_report("a.sql", [fixture_join(), second])])
        self.assertEqual(len(data["edges"]), 1)
        self.assertEqual(data["edges"][0]["join_type_summary"], {"INNER": 1, "LEFT": 1})


class ExtractionTests(unittest.TestCase):
    def parse(self, sql, schema=None, query_id="test.sql"):
        return extract(sql, query_id, schema or {})

    def test_alias_and_star_schema_comma_joins(self):
        schema = {"store_sales": frozenset(["ss_item_sk", "ss_sold_date_sk"]),
                  "item": frozenset(["i_item_sk"]), "date_dim": frozenset(["d_date_sk"])}
        report = self.parse("SELECT * FROM store_sales ss, item i, date_dim d "
                            "WHERE ss_item_sk=i_item_sk AND ss_sold_date_sk=d_date_sk", schema)
        self.assertEqual(report["status"], "success")
        self.assertEqual(len(report["joins"]), 2)
        self.assertEqual(report["joins"][0]["left"]["table"], "store_sales")

    def test_alias_scope_and_correlated_reference(self):
        report = self.parse("SELECT * FROM a x JOIN c y ON x.id=y.id WHERE EXISTS "
                            "(SELECT 1 FROM b x WHERE x.c_id=y.id)")
        self.assertEqual(len(report["joins"]), 2)
        pairs = {tuple(sorted((j["left"]["table"], j["right"]["table"]))) for j in report["joins"]}
        self.assertEqual(pairs, {("a", "c"), ("b", "c")})

    def test_cte_fallback_not_physical_lineage(self):
        report = self.parse("WITH c AS (SELECT id FROM a) SELECT * FROM c JOIN b ON c.id=b.id")
        self.assertEqual(report["status"], "partial")
        join = report["joins"][0]
        self.assertEqual(join["left"]["reference_type"], "non_physical")
        self.assertIn(":cte:c", join["left"]["table"])
        self.assertEqual(join["resolution_status"], "unresolved")
        self.assertTrue(report["unresolved"])
        self.assertIn("a", {r["table"] for r in report["references"]})

    def test_derived_fallback_and_cte_identity_is_local(self):
        sql = "SELECT * FROM (SELECT id FROM a) c JOIN b ON c.id=b.id"
        first = self.parse(sql)
        second = self.parse(sql, query_id="other.sql")
        self.assertIn(":derived:c", first["joins"][0]["left"]["table"])
        data = aggregate([first, second])
        self.assertEqual(len(data["edges"]), 2)

    def test_self_join_preserves_roles_and_counts_once_per_node(self):
        report = self.parse("SELECT * FROM item a JOIN item b ON a.id=b.parent_id")
        join = report["joins"][0]
        self.assertTrue(join["self_join"])
        self.assertNotEqual(join["left"]["role"], join["right"]["role"])
        data = aggregate([report])
        self.assertEqual(data["nodes"][0]["join_occurrence_count"], 1)
        self.assertTrue(data["edges"][0]["self_join"])

    def test_non_equality(self):
        report = self.parse("SELECT * FROM a LEFT JOIN b ON a.id>b.id")
        join = report["joins"][0]
        self.assertEqual((join["relationship_class"], join["operator"], join["join_type"]),
                         ("non_equality", ">", "LEFT"))

    def test_ambiguous_columns(self):
        schema = {"a": frozenset(["id"]), "b": frozenset(["id"])}
        report = self.parse("SELECT * FROM a,b WHERE id=b.id", schema)
        self.assertEqual(report["joins"], [])
        self.assertIn("Ambiguous", report["unresolved"][0]["reason"])

    def test_no_schema_bare_columns_uncertain(self):
        report = self.parse("SELECT * FROM a,b WHERE a_id=b_id")
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["joins"], [])

    def test_complex_expression_preserved_without_invented_edge(self):
        report = self.parse("SELECT * FROM a,b WHERE CAST(a.id AS INT)=b.id")
        self.assertEqual(report["status"], "partial")
        self.assertEqual(len(report["joins"]), 0)
        self.assertIn("CAST", report["unresolved"][0]["raw_predicate"])

    def test_do_not_infer_edges_from_cooccurrence_or_column_filters(self):
        report = self.parse("SELECT * FROM a,b WHERE a.x=3 AND a.x=a.y AND b.x>10")
        self.assertEqual(report["joins"], [])

    def test_cross_join_explicit_signal(self):
        report = self.parse("SELECT * FROM a CROSS JOIN b")
        self.assertEqual(len(report["joins"]), 1)
        self.assertEqual(report["joins"][0]["relationship_class"], "cross")

    def test_or_marks_conditional_and_projection_equality_is_not_join(self):
        report = self.parse("SELECT CASE WHEN a.x=b.x THEN 1 END FROM a,b "
                            "WHERE a.id=b.id OR a.id=b.parent")
        self.assertEqual(len(report["joins"]), 2)
        self.assertTrue(all(j["conditional"] for j in report["joins"]))

    def test_outer_where_join_type_not_misrepresented(self):
        report = self.parse("SELECT * FROM a LEFT JOIN b ON a.id=b.id WHERE a.x=b.x")
        self.assertEqual({j["join_type"] for j in report["joins"]}, {"LEFT", "UNKNOWN"})

    def test_in_subquery_preserved_as_unresolved(self):
        report = self.parse("SELECT * FROM a WHERE a.id IN (SELECT b.id FROM b)")
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["joins"], [])
        self.assertIn("IN subquery", report["unresolved"][0]["reason"])

    def test_correlated_join_type_unknown(self):
        report = self.parse("SELECT * FROM a WHERE EXISTS (SELECT 1 FROM b WHERE b.id=a.id)")
        self.assertEqual(report["joins"][0]["join_type"], "UNKNOWN")

    def test_case_and_schema_qualification(self):
        report = self.parse("SELECT * FROM dbo.A X JOIN dbo.B Y ON X.ID=Y.A_ID",
                            {"a": frozenset(["id"]), "b": frozenset(["a_id"])})
        join = report["joins"][0]
        self.assertEqual((join["left"]["table"], join["left"]["column"]), ("dbo.a", "id"))


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.input = self.base / "queries with spaces"
        self.input.mkdir()
        self.output = self.base / "output"

    def write(self, name, sql):
        path = self.input / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(sql, encoding="utf-8")

    def run_tool(self, **kwargs):
        return run(self.input, self.output, None, **kwargs)

    def test_list_only_missing_file_failure_and_continue(self):
        self.write("selected name.sql", "SELECT * FROM a JOIN b ON a.id=b.a_id")
        self.write("unlisted.sql", "SELECT * FROM c JOIN d ON c.id=d.id")
        self.write("bad.sql", "SELECT * FROM a WHERE (")
        self.write("benchmark.sql", "SELECT 1")
        self.write("queries.lst", "selected name.sql\nbad.sql\nmissing.sql\n")
        with self.assertLogs("join_graph", level="ERROR"):
            data = self.run_tool()
        self.assertEqual(data["coverage"]["discovered_queries"], 3)
        self.assertEqual(data["coverage"]["failed_queries"], 2)
        self.assertEqual(data["edges"][0]["query_frequency"], 1/3)
        manifest = json.loads((self.output / "manifest.json").read_text())
        self.assertEqual(manifest["excluded_sql_files"], ["benchmark.sql", "unlisted.sql"])
        self.assertEqual(len(manifest["queries"]), 3)
        self.assertEqual(len(list((self.output / "query_reports").glob("*.json"))), 3)
        with (self.output / "failures.csv").open() as f:
            self.assertEqual(len(list(csv.DictReader(f))), 2)

    def test_tokenizer_failure_does_not_abort_workload(self):
        self.write("bad.sql", "SELECT 'unterminated")
        self.write("good.sql", "SELECT * FROM a JOIN b ON a.id=b.id")
        with self.assertLogs("join_graph", level="ERROR"):
            data = self.run_tool()
        self.assertEqual(data["coverage"]["failed_queries"], 1)
        self.assertEqual(len(data["edges"]), 1)

    def test_recursive_discovery_paths_with_spaces_and_safe_report_names(self):
        self.write("nested dir/it's a query.sql", "SELECT * FROM a JOIN b ON a.id=b.id")
        data = self.run_tool()
        query_id = "nested dir/it's a query.sql"
        self.assertIn(query_id, data["edges"][0]["query_ids"])
        self.assertTrue((self.output / "query_reports" / report_filename(query_id)).is_file())

    def test_duplicate_list_entries_do_not_inflate_counts(self):
        self.write("q.sql", "SELECT * FROM a JOIN b ON a.id=b.id")
        self.write("queries.lst", "q.sql\nq.sql\n")
        with self.assertLogs("join_graph", level="WARNING"):
            data = self.run_tool()
        self.assertEqual(data["coverage"]["discovered_queries"], 1)

    def test_regeneration_removes_only_previous_managed_query_reports(self):
        self.write("a.sql", "SELECT * FROM a JOIN b ON a.id=b.id")
        self.write("b.sql", "SELECT 1")
        self.run_tool()
        note = self.output / "query_reports/keep.txt"
        note.write_text("not managed by the generator")
        self.write("queries.lst", "a.sql\n")
        self.run_tool()
        self.assertFalse((self.output / "query_reports" / report_filename("b.sql")).exists())
        self.assertTrue(note.exists())

    def test_list_rejects_paths_outside_input(self):
        self.write("queries.lst", "../outside.sql")
        with self.assertRaisesRegex(ValueError, "Invalid query list"):
            self.run_tool()

    def test_empty_workload_valid_exports(self):
        data = self.run_tool()
        self.assertEqual(data["nodes"], [])
        self.assertEqual(data["coverage"]["frequency_denominator"], 0)
        self.assertTrue((self.output / "join_graph.html").is_file())
        self.assertEqual((self.output / "join_edges.csv").read_text().count("\n"), 1)

    def test_stable_exports_and_graph_embedded_data(self):
        self.write("q.sql", "SELECT * FROM a JOIN b ON a.id=b.id")
        data = self.run_tool(minimum=99)
        first = {p.relative_to(self.output): p.read_bytes() for p in self.output.rglob("*") if p.is_file()}
        self.run_tool(minimum=99)
        second = {p.relative_to(self.output): p.read_bytes() for p in self.output.rglob("*") if p.is_file()}
        self.assertEqual(first, second)
        page = (self.output / "join_graph.html").read_text()
        match = re.search(r'<script id="graph-data" type="application/json">(.*?)</script>', page, re.S)
        self.assertIsNotNone(match)
        embedded = json.loads(match.group(1))
        self.assertEqual(embedded, data)
        self.assertEqual(len(embedded["nodes"]), 2)
        self.assertEqual(len(embedded["edges"]), 1)
        dot = (self.output / "join_graph.dot").read_text()
        self.assertEqual(dot.count(" -- "), 1)  # DOT is not frequency-filtered.
        self.assertIn("a.id = b.id (1 queries)", dot)
        self.assertEqual(render_dot(data, 99).count(" -- "), 0)
        svg = ET.parse(self.output / "join_graph.svg")
        self.assertEqual(len(svg.findall(".//{http://www.w3.org/2000/svg}circle")), 2)
        self.assertEqual(len(svg.findall(".//{http://www.w3.org/2000/svg}path")), 0)

    def test_script_content_escaped_in_html(self):
        self.write("q.sql", 'SELECT * FROM [</script><script>alert(1)</script>] a JOIN b ON a.id=b.id')
        self.run_tool()
        page = (self.output / "join_graph.html").read_text()
        self.assertNotIn("</script><script>alert(1)</script>", page)

    def test_cli_strict_failure_exit_after_writing_outputs(self):
        self.write("bad.sql", "SELECT * FROM a WHERE (")
        result = subprocess.run([sys.executable, str(TOOL / "analyze_workload.py"),
                                 "--input", str(self.input), "--output", str(self.output),
                                 "--no-schema", "--fail-on-parse-errors"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertTrue((self.output / "coverage_report.json").is_file())

    def test_documented_sample_expected_counts(self):
        data = run(TOOL / "samples", self.output, TOOL.parents[1] / "tpcds-kit/tools/tpcds.sql", minimum=1)
        expected = json.loads((TOOL / "samples/expected.json").read_text())
        actual = {
            "nodes": {n["table"]: n["query_count"] for n in data["nodes"]},
            "edges": {e["label"]: [e["query_count"], e["occurrence_count"]] for e in data["edges"]},
            "table_pairs": len(data["table_pairs"]),
        }
        self.assertEqual(actual, expected)

    @unittest.skipUnless(os.environ.get("RUN_TPCDS_INTEGRATION") == "1",
                         "Set RUN_TPCDS_INTEGRATION=1 for the locally generated 1GB workload")
    def test_full_local_workload(self):
        root = TOOL.parents[1]
        query_dir = root / "data/1GB/queryset"
        if not (query_dir / "queries.lst").is_file():
            self.skipTest("Generate the 1GB split queries first")
        data = run(query_dir, self.output, root / "tpcds-kit/tools/tpcds.sql")
        self.assertEqual(data["coverage"]["failed_queries"], 0)
        selected = {line.strip() for line in (query_dir / "queries.lst").read_text().splitlines() if line.strip()}
        self.assertEqual(data["coverage"]["discovered_queries"], len(selected))
        self.assertEqual(data["coverage"]["physical_tables"], 24)
        self.assertNotIn("benchmark.sql", data["query_reports"])


if __name__ == "__main__":
    unittest.main()
