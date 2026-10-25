from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))

from extractor import extract, read_schema
from report import aggregate
from usage import analyze, level, read_indexes

SCHEMA = read_schema(TOOL.parents[1] / "tpcds-kit/tools/tpcds.sql")


def cols(items):
    return sorted(f"{c['table']}.{c['column']}" for c in items)


def usage(sql, query_id="q.sql"):
    return extract(sql, query_id, SCHEMA)


class UsageExtractionTests(unittest.TestCase):
    def test_group_filter_and_aggregate_columns(self):
        report = usage("""
            SELECT dt.d_year, item.i_brand, SUM(ss_ext_sales_price - ss_net_profit), COUNT(*)
            FROM date_dim dt, store_sales, item
            WHERE dt.d_date_sk = store_sales.ss_sold_date_sk AND ss_item_sk = i_item_sk
              AND dt.d_moy = 11 AND i_category IN ('Books', 'Music') AND i_current_price BETWEEN 1 AND 2
              AND i_brand LIKE 'A%' AND i_color IS NOT NULL
            GROUP BY dt.d_year, item.i_brand""")
        found = report["usage"]
        self.assertEqual(cols(found["group_by"][0]["columns"]), ["date_dim.d_year", "item.i_brand"])
        kinds = {cols(f["columns"])[0]: f["kind"] for f in found["filters"]}
        self.assertEqual(kinds, {"date_dim.d_moy": "equality", "item.i_category": "in_list",
                                 "item.i_current_price": "range", "item.i_brand": "like",
                                 "item.i_color": "is_null"})
        sums = [a for a in found["aggregates"] if a["function"] == "sum"]
        self.assertEqual(cols(sums[0]["columns"]), ["store_sales.ss_ext_sales_price", "store_sales.ss_net_profit"])
        self.assertIn("count", {a["function"] for a in found["aggregates"]})

    def test_joins_are_not_filters_and_subquery_values_are(self):
        report = usage("""
            SELECT ss_item_sk FROM store_sales, date_dim
            WHERE ss_sold_date_sk = d_date_sk
              AND d_month_seq BETWEEN (SELECT MIN(d_month_seq) FROM date_dim WHERE d_year = 2000) AND 1200""")
        filters = {(cols(f["columns"])[0], f["kind"]) for f in report["usage"]["filters"]}
        self.assertEqual(filters, {("date_dim.d_month_seq", "range"), ("date_dim.d_year", "equality")})

    def test_cte_lineage_and_aggregate_value_filters(self):
        report = usage("""
            WITH totals AS (
              SELECT d_year AS yr, s_store_id, SUM(ss_net_paid) AS total
              FROM store_sales, date_dim, store
              WHERE ss_sold_date_sk = d_date_sk AND ss_store_sk = s_store_sk
              GROUP BY d_year, s_store_id)
            SELECT s_store_id, SUM(total) FROM totals WHERE yr = 2001 AND total > 0 GROUP BY s_store_id""")
        found = report["usage"]
        outer = [f for f in found["filters"] if f["raw"].startswith(("yr", "total"))]
        self.assertEqual({f["kind"] for f in outer}, {"equality", "aggregate_value"})
        self.assertEqual(cols(next(f for f in outer if f["kind"] == "equality")["columns"]), ["date_dim.d_year"])
        self.assertEqual(next(f for f in outer if f["kind"] == "aggregate_value")["columns"], [])
        self.assertTrue(all(cols(a["columns"]) == ["store_sales.ss_net_paid"] for a in found["aggregates"]))
        self.assertIn(["store.s_store_id"], [cols(g["columns"]) for g in found["group_by"]])

    def test_rollup_flag(self):
        report = usage("SELECT i_category, SUM(ss_quantity) FROM store_sales JOIN item ON ss_item_sk = i_item_sk "
                       "GROUP BY ROLLUP(i_category)")
        self.assertTrue(report["usage"]["group_by"][0]["rollup"])


class UsageAggregationTests(unittest.TestCase):
    def reports(self):
        yearly = usage("""SELECT d_year, i_brand, SUM(ss_sales_price) FROM date_dim, store_sales, item
            WHERE d_date_sk = ss_sold_date_sk AND ss_item_sk = i_item_sk AND d_moy = 11
            GROUP BY d_year, i_brand""", "q1.sql")
        daily = usage("""SELECT d_date, AVG(ss_quantity) FROM date_dim, store_sales
            WHERE d_date_sk = ss_sold_date_sk AND d_year = 2000 GROUP BY d_date""", "q2.sql")
        detail = usage("""SELECT ss_ticket_number FROM date_dim, store_sales
            WHERE d_date_sk = ss_sold_date_sk AND d_year = 2001""", "q3.sql")
        return [yearly, daily, detail]

    def test_levels(self):
        self.assertEqual(level("date_dim", ["d_moy", "d_year"]), "month")
        self.assertEqual(level("dbo.date_dim", ["d_year"]), "year")
        self.assertEqual(level("item", ["i_brand", "i_color"]), "brand/manufacturer + 1 other attribute")
        self.assertEqual(level("promotion", ["p_channel_tv"]), "p_channel_tv")

    def test_table_edge_and_fact_granularity(self):
        reports = self.reports()
        data = aggregate(reports)
        indexes = [{"name": "NCCI", "table": "store_sales", "kind": "nonclustered columnstore", "keys": [],
                    "include": ["ss_sold_date_sk", "ss_item_sk", "ss_sales_price"], "source": "test"}]
        analyze(reports, data, {"indexes": indexes, "row_counts": {"date_dim": 73049, "store": 12}})
        date = data["table_usage"]["date_dim"]
        self.assertEqual({(l["level"], l["query_count"]) for l in date["group_level_summary"]},
                         {("year", 1), ("day", 1)})
        self.assertEqual(date["no_group_by_query_count"], 1)
        self.assertEqual({(l["level"], l["query_count"]) for l in date["filter_level_summary"]},
                         {("month", 1), ("year", 2)})
        edge = next(e for e in data["edges"] if e["left_column"] == "d_date_sk")
        self.assertEqual({g["signature"]: g["query_count"] for g in edge["grouping_levels"]},
                         {"date_dim: year · item: brand/manufacturer": 1, "date_dim: day": 1})
        self.assertEqual(edge["no_group_by_query_count"], 1)
        usage_rows = {(r["table"], r["column"]): r for r in data["column_usage"]}
        self.assertEqual(usage_rows[("date_dim", "d_year")]["filter_query_count"], 2)
        self.assertEqual(usage_rows[("store_sales", "ss_quantity")]["aggregate_functions"], {"avg": 1})
        fact = data["fact_grains"][0]
        self.assertEqual(fact["table"], "store_sales")
        self.assertIn({"dimension": "date_dim", "level": "month", "query_count": 1, "query_ids": ["q1.sql"]},
                      fact["dimension_levels"])
        columnstore = next(h for h in data["optimization_hints"] if h["kind"] == "columnstore")
        self.assertIn("1 of 3 queries", columnstore["evidence"])
        self.assertIn("ss_quantity (1 queries)", columnstore["action"])
        self.assertIn("ss_ticket_number (1 queries)", columnstore["action"])

    def test_plan_table_names_are_normalized(self):
        reports = self.reports()
        data = aggregate(reports)
        for node in data["nodes"]:
            node["table"] = "dbo." + node["table"]
        analyze(reports, data, None)
        self.assertEqual(sorted(data["table_usage"]), ["dbo.date_dim", "dbo.item", "dbo.store_sales"])

    def test_read_indexes(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "indexes.sql"
            script.write_text("CREATE NONCLUSTERED COLUMNSTORE INDEX NCCI_x ON dbo.store_sales (a, b);\n"
                              "CREATE INDEX IX_y ON dbo.item (i_category, i_class) INCLUDE (i_brand);\n")
            schema = Path(directory) / "schema.sql"
            schema.write_text("create table item\n(\n    i_item_sk integer,\n    primary key (i_item_sk)\n);\n")
            found = {i["name"]: i for i in read_indexes(script, schema)}
        self.assertEqual(found["NCCI_x"]["include"], ["a", "b"])
        self.assertEqual(found["IX_y"]["keys"], ["i_category", "i_class"])
        self.assertEqual(found["IX_y"]["include"], ["i_brand"])
        self.assertEqual(found["PK_item"]["keys"], ["i_item_sk"])


if __name__ == "__main__":
    unittest.main()
