from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))

from collect_plans import actual_batch, collect, estimated_batch, sqlcmd, validate_query
from compare_plans import compare
from plan_report import generate, parse_plan, query_metrics, row_deviation, split_documents, summarize
from report import write_json

URI = "http://schemas.microsoft.com/sqlserver/2004/07/showplan"


def column(table, col, alias=None):
    return f'<ColumnReference Schema="[dbo]" Table="[{table}]" Alias="[{alias or table}]" Column="{col}"/>'


def scan(table, node_id=1):
    return (f'<RelOp NodeId="{node_id}" PhysicalOp="Index Scan" LogicalOp="Index Scan" EstimateRows="100">'
            f'<IndexScan><Object Schema="[dbo]" Table="[{table}]" Index="[IX_{table}]"/></IndexScan></RelOp>')


def document(body, reason="", set_options=""):
    return (f'<ShowPlanXML xmlns="{URI}"><BatchSequence><Batch><Statements>'
            f'<StmtSimple StatementText="SELECT ..." StatementSubTreeCost="10" StatementEstRows="12" '
            f'StatementOptmEarlyAbortReason="{reason}">{set_options}<QueryPlan>{body}</QueryPlan></StmtSimple>'
            '</Statements></Batch></BatchSequence></ShowPlanXML>')


def hash_join(rows="12", second_key=False, expr=False):
    left = '<ColumnReference Column="Expr1001"/>' if expr else column("a", "id")
    right = column("b", "a_id")
    if second_key:
        left += column("a", "code")
        right += column("b", "a_code")
    return (f'<RelOp NodeId="0" PhysicalOp="Hash Match" LogicalOp="Inner Join" EstimateRows="{rows}" '
            f'EstimatedTotalSubtreeCost="10"><Hash><HashKeysBuild>{left}</HashKeysBuild>'
            f'<HashKeysProbe>{right}</HashKeysProbe>{scan("a")}{scan("b",2)}</Hash></RelOp>')


def actual_document(counters=None, rows="12", second_key=False):
    if counters is None:
        counters = [{"Thread": "0", "ActualRows": "120", "ActualExecutions": "1",
                     "ActualElapsedms": "12", "ActualCPUms": "9"}]
    runtime = "<RunTimeInformation>" + "".join(
        "<RunTimeCountersPerThread " + " ".join(f'{k}="{v}"' for k,v in c.items()) + "/>"
        for c in counters) + "</RunTimeInformation>"
    body = hash_join(rows, second_key=second_key).replace("<Hash>",runtime+"<Hash>",1)
    return document(body).replace("<QueryPlan>", '<QueryPlan><QueryTimeStats ElapsedTime="22" CpuTime="18"/>')


class RuntimeTests(unittest.TestCase):
    def test_actual_rows_and_statement_time(self):
        r = parse_plan(actual_document(), "q.sql", actual=True)
        op = r["join_operators"][0]
        self.assertEqual(op["actual_rows_total"], 120)
        self.assertEqual(op["actual_rows_per_execution"], 120)
        self.assertEqual(op["row_error_factor"], 10)
        self.assertEqual(op["row_deviation_pct"], 900)
        self.assertEqual(op["row_deviation_direction"], "underestimated")
        q = query_metrics(r)
        self.assertEqual(q["actual_elapsed_ms"], 22)
        self.assertEqual(q["actual_cpu_ms"], 18)
        self.assertEqual(q["worst_row_error_factor"], 10)
        self.assertEqual(q["estimated_cost"], 10)  # Cost never treated as milliseconds.

    def test_parallel_threads_and_repeated_invocations(self):
        counters = [{"Thread": str(i), "ActualRows": str(rows), "ActualExecutions": "3",
                     "ActualElapsedms": str(elapsed), "ActualCPUms": "4"}
                    for i, rows, elapsed in [(1,180,10),(2,120,12)]]
        r = parse_plan(actual_document(counters,rows="10"),"q.sql",actual=True)
        op = r["join_operators"][0]
        self.assertEqual(op["actual_rows_total"], 300)
        self.assertEqual(op["actual_executions"], 3)
        self.assertEqual(op["actual_rows_per_execution"], 100)
        self.assertEqual(op["actual_elapsed_ms"], 12)
        self.assertEqual(op["actual_cpu_ms"], 8)
        self.assertEqual(op["row_error_factor"], 10)

    def test_unexecuted_branch_not_reported_as_overestimate(self):
        r = parse_plan(actual_document([{"ActualRows":"0","ActualExecutions":"0"}]),"q.sql",actual=True)
        op=r["join_operators"][0]
        self.assertEqual(op["runtime_state"], "not_executed")
        self.assertIsNone(op["row_error_factor"])
        self.assertEqual(summarize([op])["not_executed_operator_count"], 1)
        self.assertEqual(query_metrics(r)["executed_join_operator_count"], 0)

    def test_zero_comparisons_explicit_not_epsilon(self):
        self.assertEqual(row_deviation(0,0)["row_error_factor"], 1)
        for actual,estimate in [(0,10),(10,0)]:
            m=row_deviation(actual,estimate)
            self.assertTrue(m["row_error_unbounded"])
            self.assertIsNone(m["row_error_factor"])
        self.assertEqual(row_deviation(0,10)["row_deviation_pct"], -100)
        self.assertIsNone(row_deviation(10,0)["row_deviation_pct"])
        self.assertIsNone(row_deviation(None,0)["row_deviation_pct"])

    def test_missing_or_partial_runtime_is_unavailable(self):
        r=parse_plan(actual_document([{"ActualRows":"120"}]),"q.sql",actual=True)
        self.assertEqual(r["join_operators"][0]["runtime_state"], "unavailable")
        self.assertIsNone(query_metrics(r)["worst_row_error_factor"])
        with self.assertRaisesRegex(ValueError, "counters are missing"):
            parse_plan(document(hash_join()),"q.sql",actual=True)

    def test_unmapped_join_errors_still_in_query_ranking(self):
        doc=actual_document().replace(column("a","id"),'<ColumnReference Column="Expr1001"/>')
        r=parse_plan(doc,"q.sql",actual=True)
        self.assertFalse(r["joins"])
        self.assertEqual(query_metrics(r)["worst_row_error_factor"],10)

    def test_actual_pair_metrics_deduplicate_multikey_operator(self):
        r=parse_plan(actual_document(second_key=True),"q.sql",actual=True)
        op=r["join_operators"][0]
        m=summarize([op,op])
        self.assertEqual(m["executed_operator_count"],1)
        self.assertEqual(m["actual_rows_per_execution_avg"],120)
        self.assertEqual(m["row_error_factor_max"],10)


class PlanParserTests(unittest.TestCase):
    def test_hash_keys_and_input_estimates(self):
        r = parse_plan(document(hash_join()), "q.sql")
        self.assertEqual(len(r["joins"]), 1)
        j = r["joins"][0]
        self.assertEqual((j["left"]["table"], j["left"]["column"]), ("dbo.a", "id"))
        self.assertEqual(j["right"]["table"], "dbo.b")
        op = r["join_operators"][0]
        self.assertEqual(op["estimated_rows"], 12)
        self.assertEqual(len(op["inputs"]), 2)
        self.assertEqual(op["inputs"][0]["estimated_rows"], 100)
        self.assertEqual(op["inputs"][0]["tables_and_indexes"][0]["index"], "ix_a")

    def test_multiple_keys_one_operator(self):
        r = parse_plan(document(hash_join(second_key=True)), "q.sql")
        self.assertEqual(len(r["joins"]), 2)
        self.assertEqual(len(r["join_operators"]), 1)

    def test_merge_join(self):
        body = hash_join().replace('PhysicalOp="Hash Match"', 'PhysicalOp="Merge Join"')
        body = body.replace("<Hash>", "<Merge>").replace("</Hash>", "</Merge>")
        body = body.replace("HashKeysBuild", "OuterSideJoinColumns").replace("HashKeysProbe", "InnerSideJoinColumns")
        r = parse_plan(document(body), "q.sql")
        self.assertEqual(len(r["joins"]), 1)
        self.assertEqual(r["join_operators"][0]["physical_operator"], "Merge Join")

    def test_nested_loop_parameterized_seek(self):
        inner = (f'<RelOp NodeId="2" PhysicalOp="Index Seek" LogicalOp="Index Seek" EstimateRows="3">'
                 f'<IndexScan><Object Schema="[dbo]" Table="[b]"/><SeekPredicates><SeekPredicateNew>'
                 f'<SeekKeys><Prefix ScanType="EQ"><RangeColumns>{column("b","a_id")}</RangeColumns>'
                 f'<RangeExpressions><ScalarOperator><Identifier>{column("a","id")}</Identifier>'
                 f'</ScalarOperator></RangeExpressions></Prefix></SeekKeys></SeekPredicateNew>'
                 f'</SeekPredicates></IndexScan></RelOp>')
        body = (f'<RelOp NodeId="0" PhysicalOp="Nested Loops" LogicalOp="Left Outer Join" EstimateRows="12">'
                f'<NestedLoops><OuterReferences>{column("a","id")}</OuterReferences>'
                f'{scan("a")}{inner}</NestedLoops></RelOp>')
        r = parse_plan(document(body), "q.sql")
        self.assertEqual(len(r["joins"]), 1)
        self.assertEqual(r["joins"][0]["evidence_signal"], "parameterized inner seek")
        self.assertEqual(r["joins"][0]["join_type"], "Left Outer Join")

    def test_no_cross_product_inferred_from_subtree_tables(self):
        r = parse_plan(document(hash_join(expr=True)), "q.sql")
        self.assertEqual(len(r["joins"]), 0)
        self.assertEqual(len(r["unresolved"]), 1)
        self.assertEqual(len(r["references"]), 2)

    def test_same_predicate_in_key_and_residual_deduplicated(self):
        compare = (f'<ProbeResidual><ScalarOperator><Compare CompareOp="EQ">'
                   f'<ScalarOperator><Identifier>{column("a","id")}</Identifier></ScalarOperator>'
                   f'<ScalarOperator><Identifier>{column("b","a_id")}</Identifier></ScalarOperator>'
                   f'</Compare></ScalarOperator></ProbeResidual>')
        body = hash_join().replace("</Hash>", compare + "</Hash>")
        self.assertEqual(len(parse_plan(document(body), "q.sql")["joins"]), 1)

    def test_self_join_alias_roles_preserved(self):
        body = hash_join().replace(column("a", "id"), column("a", "id", "x"))
        body = body.replace(column("b", "a_id"), column("a", "parent", "y"))
        r = parse_plan(document(body), "q.sql")
        self.assertTrue(r["joins"][0]["self_join"])
        self.assertNotEqual(r["joins"][0]["left"]["role"], r["joins"][0]["right"]["role"])

    def test_negative_nan_or_missing_estimate(self):
        for rows in ["NaN", "-1", "inf"]:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                parse_plan(document(hash_join(rows)), "q.sql")
        r = parse_plan(document(hash_join().replace(' EstimateRows="12"', "")), "q.sql")
        self.assertIsNone(summarize(r["join_operators"])["estimated_rows_avg"])

    def test_operator_metrics_deduplicate_ids(self):
        first = parse_plan(document(hash_join("10")), "first.sql")["join_operators"][0]
        second = parse_plan(document(hash_join("30")), "second.sql")["join_operators"][0]
        s = summarize([first, first, second])
        self.assertEqual((s["estimated_rows_min"], s["estimated_rows_max"], s["estimated_rows_avg"]), (10,30,20))
        self.assertEqual(s["operator_count"], 2)
        self.assertEqual(s["physical_operator_summary"], {"Hash Match": 2})

    def test_sqlcmd_wrapping_inside_tags_and_entities(self):
        raw = document(hash_join())
        wrapped = "\n".join(raw[i:i+17] for i in range(0, len(raw), 17))
        self.assertEqual(split_documents(wrapped + "\n(1 rows affected)"), [raw])

    def test_multiple_documents_and_truncation(self):
        doc = document(hash_join())
        self.assertEqual(len(split_documents(doc + "\n" + doc)), 2)
        with self.assertRaises(ValueError):
            split_documents(doc + "<ShowPlanXML")
        with self.assertRaises(ValueError):
            split_documents("truncated output")
        with self.assertRaises(ValueError):
            split_documents('<ShowPlanXML xmlns="wrong"></ShowPlanXML>')

    def test_statement_options_optimizer_timeout_and_adaptive_threshold(self):
        body = hash_join().replace('PhysicalOp="Hash Match"', 'PhysicalOp="Adaptive Join" '
                                  'EstimatedJoinType="Hash Match" AdaptiveThresholdRows="42"')
        body = body.replace("<Hash>", "<AdaptiveJoin>").replace("</Hash>", "</AdaptiveJoin>")
        r = parse_plan(document(body, "TimeOut", '<StatementSetOptions QUOTED_IDENTIFIER="false"/>'), "q.sql")
        self.assertEqual(r["statements"][0]["optimization_early_abort_reason"], "TimeOut")
        self.assertEqual(r["statements"][0]["set_options"], {"QUOTED_IDENTIFIER": "false"})
        self.assertEqual(r["join_operators"][0]["adaptive_threshold_rows"], 42)
        self.assertEqual(len(r["joins"]), 1)

    def test_actual_plan_not_mislabeled_estimated(self):
        doc = document(hash_join()).replace("</QueryPlan>", "<RunTimeInformation/></QueryPlan>")
        with self.assertRaisesRegex(ValueError, "Actual execution plan"):
            parse_plan(doc, "q.sql")
        with self.assertRaises(ValueError):
            parse_plan(f'<ShowPlanXML xmlns="{URI}"/>', "q.sql")


class CollectorTests(unittest.TestCase):
    def test_only_select_workload_allowed(self):
        for sql in ["DELETE FROM a", "SELECT 1; SET SHOWPLAN_XML OFF", "SELECT * INTO b FROM a",
                    "GO\nSELECT 1", ":r arbitrary.sql", "!! echo bad", "-- comment\n:connect other"]:
            with self.subTest(sql=sql), self.assertRaises(ValueError):
                validate_query(sql)
        sql = "WITH c AS (SELECT 1 AS id) SELECT * FROM c;"
        batch = estimated_batch(sql)
        self.assertIn("SET SHOWPLAN_XML ON;\nGO\n", batch)
        self.assertNotIn("SHOWPLAN_XML OFF", batch)
        self.assertTrue(batch.endswith(sql+"\nGO\n"))
        self.assertIn("SET STATISTICS XML ON",actual_batch(sql))
        self.assertNotIn("SHOWPLAN_XML",actual_batch(sql))

    @patch("collect_plans.subprocess.run")
    def test_docker_command_has_remote_deadline_and_no_password(self, runner):
        sqlcmd("tpcds_sqlserver", "database with spaces", "SELECT 1", 20)
        args, kwargs = runner.call_args
        command = args[0]
        self.assertEqual(command[-1], "database with spaces")
        self.assertIn("timeout --signal=TERM --kill-after=5s", command[6])
        self.assertEqual(kwargs["timeout"], 40)
        self.assertEqual(kwargs["input"], "SELECT 1")
        self.assertNotIn("-P", command)

    def test_collection_timeout_failure_kept_and_remaining_queries_continue(self):
        with tempfile.TemporaryDirectory() as temp:
            input_dir = Path(temp)/"input"; input_dir.mkdir()
            (input_dir/"a.sql").write_text("SELECT 1")
            (input_dir/"b.sql").write_text("SELECT 2")
            output = Path(temp)/"output"
            success = subprocess.CompletedProcess([],0,document(hash_join()),"")
            with patch("collect_plans.read_context", return_value={"indexes":[]}), patch(
                    "collect_plans.sqlcmd", side_effect=[subprocess.TimeoutExpired("docker",40),success]):
                with self.assertLogs("join_graph.plans", level="ERROR"):
                    data = collect(input_dir,output,"container","db",20)
            self.assertEqual(data["coverage"]["failed_queries"], 1)
            self.assertEqual(data["coverage"]["parsed_queries"], 1)
            self.assertEqual(data["edges"][0]["query_frequency"], .5)
            manifest = json.loads((output/"plan_manifest.json").read_text())
            self.assertEqual(len(manifest["queries"]), 2)

    def test_actual_timeout_and_result_rows_are_not_saved_as_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            input_dir=Path(temp)/"input";input_dir.mkdir()
            (input_dir/"a.sql").write_text("SELECT 1")
            (input_dir/"b.sql").write_text("SELECT 2")
            output=Path(temp)/"output"
            results=[subprocess.CompletedProcess([],0,"","Timeout expired"),
                     subprocess.CompletedProcess([],0,"result rows not part of report\n"+actual_document(),"")]
            with patch("collect_plans.read_context",return_value={}),patch(
                    "collect_plans.sqlcmd",side_effect=results),self.assertLogs("join_graph.plans",level="ERROR"):
                data=collect(input_dir,output,"container","db",10,actual=True)
            self.assertEqual(data["coverage"]["timed_out_queries"],1)
            self.assertEqual(data["query_metrics"][0]["failure_kind"],"timeout")
            self.assertIsNone(data["query_metrics"][0]["actual_elapsed_ms"])
            self.assertEqual(data["query_metrics"][1]["actual_elapsed_ms"],22)
            for path in (output/"raw_plans").glob("*.stdout.txt"):
                self.assertNotIn("result rows not part of report",path.read_text())
            self.assertEqual(data["plan_evidence"]["mode"],"actual")


class PlanReportTests(unittest.TestCase):
    def test_actual_exports_and_timeout_diagnostics_on_offline_regeneration(self):
        with tempfile.TemporaryDirectory() as temp:
            output=Path(temp)
            (output/"test.sqlplan").write_text(actual_document(second_key=True))
            (output/"timeout.stderr").write_text("Timeout expired\n")
            manifest={
                "database":"test","mode":"actual_STATISTICS_XML","collected_at_utc":"fixed","selection":{},
                "queries":[
                    {"query_id":"good.sql","status":"collected","plan_files":["test.sqlplan"]},
                    {"query_id":"slow.sql","status":"failed","plan_files":[],
                     "stderr":"timeout.stderr","error":"No XML returned"},
                ],
            }
            write_json(output/"plan_manifest.json",manifest)
            with self.assertLogs("join_graph.plans",level="ERROR"):
                data=generate(output)
            self.assertEqual(data["coverage"]["timed_out_queries"],1)
            self.assertEqual(data["table_pairs"][0]["plan_metrics"]["executed_operator_count"],1)
            self.assertEqual(data["table_pairs"][0]["plan_metrics"]["row_error_factor_max"],10)
            self.assertEqual(data["query_metrics"][0]["actual_elapsed_ms"],22)
            self.assertEqual(data["query_metrics"][1]["failure_kind"],"timeout")
            self.assertIsNone(data["query_metrics"][1]["actual_elapsed_ms"])
            self.assertIn("Timeout expired",data["query_metrics"][1]["error"])
            self.assertTrue((output/"query_metrics.csv").exists())
            page=(output/"join_graph.html").read_text()
            self.assertIn('"mode": "actual"',page)
            self.assertIn("Worst row estimate error",page)

    def test_export_deduplicates_pair_operator_rows_and_is_reproducible(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            (output/"test.sqlplan").write_text(document(hash_join(second_key=True)))
            manifest = {
                "database":"test", "mode":"estimated_only_SHOWPLAN_XML", "collected_at_utc":"fixed",
                "selection":{}, "queries":[{
                    "query_id":"query.sql","status":"collected","plan_files":["test.sqlplan"],
                    "original_sql":"SELECT ...","source_sha256":"fixture",
                }],
            }
            write_json(output/"plan_manifest.json",manifest)
            data = generate(output)
            self.assertEqual(len(data["edges"]), 2)
            pair = data["table_pairs"][0]
            self.assertEqual(pair["occurrence_count"], 2)
            self.assertEqual(pair["plan_metrics"]["operator_count"], 1)
            self.assertEqual(pair["plan_metrics"]["estimated_rows_avg"], 12)
            self.assertEqual(data["coverage"]["join_operator_count"], 1)
            before = {p.name:p.read_bytes() for p in output.iterdir() if p.is_file()}
            generate(output)
            self.assertEqual(before, {p.name:p.read_bytes() for p in output.iterdir() if p.is_file()})
            page=(output/"join_graph.html").read_text()
            self.assertIn('"plan_metrics"',page)
            self.assertIn("Estimated join-operator output rows",page)
            self.assertTrue((output/"plan_edge_metrics.csv").is_file())

    def test_compare_matching_hashes_only_and_keep_exclusions(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            for label, cost in (("baseline", "10"), ("indexed", "5")):
                output = base/label
                output.mkdir()
                (output/"test.sqlplan").write_text(document(hash_join()).replace('StatementSubTreeCost="10"',
                                                                                 f'StatementSubTreeCost="{cost}"'))
                manifest = {
                    "database":label, "mode":"estimated_only_SHOWPLAN_XML", "collected_at_utc":"fixed",
                    "selection":{}, "queries":[{
                        "query_id":query_id, "status":"collected", "plan_files":["test.sqlplan"],
                        "original_sql":"SELECT ...", "source_sha256":
                        label if query_id=="changed.sql" else "same-sql-hash",
                    } for query_id in ["same.sql", "changed.sql"]],
                }
                write_json(output/"plan_manifest.json",manifest)
                generate(output)
            result = compare(base/"baseline",base/"indexed",base/"comparison")
            self.assertEqual(result["matched_queries"], 1)
            self.assertEqual(result["queries"][0]["baseline_estimated_cost"], 10)
            self.assertEqual(result["queries"][0]["indexed_estimated_cost"], 5)
            self.assertEqual(result["exclusions"], [{"query_id":"changed.sql",
                                                    "reason":"SQL hashes differ or are missing"}])


if __name__ == "__main__":
    unittest.main()
