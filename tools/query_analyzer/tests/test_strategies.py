import unittest

from tools.query_analyzer.core.strategies import strategies
from tools.query_analyzer.web.strategies import cards


def operator(node, physical, logical='', children=(), **extra):
    return dict(node=str(node), physical=physical, logical=logical,
                children=[str(child) for child in children], objects=[],
                estimated_rows=20000, predicates=[], **extra)


def query(name, operators=(), missing=()):
    return dict(query_id=name, statements=[dict(statement_id='1', operators=list(operators), missing_indexes=list(missing))])


class StrategyTests(unittest.TestCase):
    def test_scan_alone_and_hash_join_do_not_justify_columnstore(self):
        scan = operator(1, 'Index Scan')
        scan['objects'] = [dict(Schema='[dbo]', Table='[sales]', Storage='RowStore')]
        join = operator(0, 'Hash Match', 'Inner Join', [1])
        self.assertEqual(strategies([query('q', [join, scan])]), [])
        aggregate = operator(2, 'Hash Match', 'Aggregate', [1])
        result = strategies([query('q', [aggregate, scan])])
        self.assertEqual([f['kind'] for f in result], ['columnstore'])
        self.assertIn('Scan node 1', result[0]['evidence'][0]['detail'])
        self.assertEqual(result[0]['evidence'][0]['node'], '1')
        scan['objects'][0]['Storage'] = 'ColumnStore'
        self.assertEqual(strategies([query('q', [aggregate, scan])]), [])

    def test_unrelated_scan_branch_is_not_attributed_to_aggregate(self):
        aggregate = operator(0, 'Stream Aggregate', 'Aggregate')
        scan = operator(1, 'Index Scan')
        scan['objects'] = [dict(Table='[sales]')]
        self.assertEqual(strategies([query('q', [aggregate, scan])]), [])

    def test_repeated_operators_in_one_query_are_not_shared_workload(self):
        column = dict(Schema='[dbo]', Table='[sales]', Column='[item]')
        agg = operator(0, 'Stream Aggregate', 'Aggregate', grouping=[column], measures=['SUM([amount])'])
        other = dict(agg, node='1')
        self.assertEqual(strategies([query('q', [agg, other])]), [])
        result = strategies([query('q', [agg, other]), query('r', [agg])])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['queries'], ['q', 'r'])
        self.assertIn('Matching grouping columns alone', result[0]['limitation'])

    def test_sort_order_and_unresolved_expressions_remain_separate(self):
        col = dict(Table='[t]', Column='[a]', ascending='1')
        asc = operator(0, 'Sort', ordering=[col])
        desc = operator(0, 'Sort', ordering=[dict(col, ascending='0')])
        self.assertEqual(strategies([query('q', [asc]), query('r', [desc])]), [])
        self.assertEqual(strategies([query('q', [asc]), query('r', [asc])])[0]['kind'], 'ordering')
        unresolved = operator(0, 'Sort', ordering=[dict(Column='Expr1001', ascending='1')])
        self.assertEqual(strategies([query('q', [unresolved]), query('r', [unresolved])]), [])

    def test_index_key_patterns_share_queries_without_merging_include_requests(self):
        index = dict(schema='[dbo]', table='[t]', columns={'EQUALITY': ['[a]', '[b]'], 'INCLUDE': ['[x]']})
        variant = dict(index, columns={'EQUALITY': ['[b]', '[a]'], 'INCLUDE': ['[y]']})
        result = strategies([query('q', missing=[index, index]), query('r', missing=[variant])])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['queries'], ['q', 'r'])
        self.assertEqual(len(result[0]['evidence']), 2)
        self.assertIn('[x]', result[0]['evidence'][0]['detail'])
        self.assertIn('[y]', result[0]['evidence'][1]['detail'])
        self.assertEqual(result[0]['origin'], 'SQL Server missing-index suggestion')

    def test_selectivity_requires_read_estimate_and_residual_predicate(self):
        scan = operator(0, 'Index Scan', estimated_rows_read=100000)
        scan['objects'] = [dict(Table='[sales]')]
        scan['estimated_rows'] = 100
        scan['predicates'] = [dict(role='Predicate', expression='[a] = 3')]
        self.assertEqual(strategies([query('q', [scan])])[0]['kind'], 'filter')
        scan['estimated_rows_read'] = None
        self.assertEqual(strategies([query('q', [scan])]), [])

    def test_evidence_escapes_content_and_links_to_dataset_report(self):
        index = dict(schema='[dbo]', table='<script>', columns={'EQUALITY': ['[a]']})
        page = cards(strategies([query('q', missing=[index])]), 'report_2GB.html')
        self.assertIn('report_2GB.html#detail-q', page)
        self.assertNotIn('<script>', page)
        self.assertIn('&lt;script&gt;', page)
        self.assertIn('statement 1', page)
