import unittest
from tools.query_analyzer.core.common import SCALES
from tools.query_analyzer.core.index_comparison import compare_indexes
from tools.query_analyzer.web.index_comparison import render


def request(includes, equality=None):
    return dict(schema='[dbo]', table='[sales]', columns={'EQUALITY': equality or ['[sold_date_sk]'], 'INCLUDE': includes})


def sources(queries):
    result = {scale: dict(plans={}) for scale in SCALES}
    result['1GB']['plans'] = {q: dict(statements=[dict(missing_indexes=indexes)]) for q, indexes in queries.items()}
    return result


class IndexComparisonTests(unittest.TestCase):
    def test_counts_deduplicate_suggestions_and_include_order(self):
        data = sources({'q1': [request(['[a]', '[b]']), request(['[b]', '[a]'])], 'q2': [request([])]})
        group = compare_indexes(data)[0]
        first = group['datasets']['1GB']
        self.assertEqual(first['queries'], ['q1', 'q2'])
        self.assertEqual(len(first['variants']), 2)
        self.assertEqual(first['union'], ['[a]', '[b]'])
        self.assertEqual(first['common'], [])
        self.assertFalse(group['datasets']['10GB']['available'])
        self.assertIn('Pending', render([group]))

    def test_coverage_requires_all_requests_of_a_query(self):
        data = sources({'q1': [request(['[a]']), request(['[b]'])], 'q2': [request(['[a]'])], 'q3': [request(['[a]', '[b]'])]})
        first = compare_indexes(data)[0]['datasets']['1GB']
        variants = {tuple(v['columns']): v for v in first['variants']}
        self.assertEqual(variants[('[a]',)]['covered_queries'], ['q2'])
        self.assertEqual(variants[('[a]', '[b]')]['covered_queries'], ['q1', 'q2', 'q3'])
        self.assertEqual(next(f for f in first['frequencies'] if f['column'] == '[a]')['queries'], ['q1', 'q2', 'q3'])

    def test_key_columns_are_not_counted_as_extra_include_columns(self):
        data = sources({'q': [request(['[a]', '[k1]'], ['[k1]', '[k2]']), request(['[a]'], ['[k2]', '[k1]'])]})
        groups = compare_indexes(data)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]['datasets']['1GB']['union'], ['[a]'])

    def test_html_escapes_column_names_and_keeps_query_evidence(self):
        group = compare_indexes(sources({'q1': [request(['<script>'])]}))[0]
        page = render([group])
        self.assertNotIn('<script>', page)
        self.assertIn('&lt;script&gt;', page)
        self.assertIn('report.html#detail-q1', page)
        self.assertIn('index_recommendations.csv', page)
