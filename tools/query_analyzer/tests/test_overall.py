import unittest
import xml.etree.ElementTree as ET
from tools.query_analyzer.core.common import SCALES
from tools.query_analyzer.core.overall import overall_analysis
from tools.query_analyzer.web.overall import chart, render


def payloads():
    return [dict(scale=s, queries=[], strategies=[], errors=[]) for s in SCALES]


def query(name, operators):
    return dict(query_id=name, statements=[dict(statement_id='1', operators=operators, findings=[])])


class OverallTests(unittest.TestCase):
    def test_grouping_counts_queries_not_repeated_operators(self):
        data = payloads()
        op = dict(node='1', physical='Hash Match', grouping=[dict(Schema='[dbo]', Table='[t]', Column='a')], measures=['SUM([b])'])
        data[0]['queries'] = [query('q1', [op, dict(op, node='2')]), query('q2', [op])]
        data[1]['queries'] = [query('q1', [op])]
        result = overall_analysis(data, [])
        pattern = result['patterns'][0]
        self.assertEqual(pattern['datasets']['1GB']['queries'], ['q1', 'q2'])
        self.assertEqual(len(pattern['datasets']['1GB']['evidence']), 3)
        self.assertEqual(pattern['datasets']['2GB']['queries'], ['q1'])
        self.assertFalse(pattern['datasets']['5GB']['available'])
        page, _ = render(result)
        self.assertIn('[dbo].[t].a', page)
        self.assertIn('report.html#detail-q1', page)

    def test_scan_evidence_is_deduplicated_per_query_and_missing_differs_from_zero(self):
        data = payloads()
        data[0]['queries'] = [query('q1', [])]
        data[1]['queries'] = [query('q1', [])]
        evidence = dict(query_id='q1', statement_id='1', node='2', detail='scan to aggregate')
        data[0]['strategies'] = [dict(kind='columnstore', table='[dbo].[store_sales]', evidence=[evidence, dict(evidence, node='3')])]
        result = overall_analysis(data, [])
        pattern = result['patterns'][0]
        self.assertEqual(pattern['datasets']['1GB']['queries'], ['q1'])
        self.assertEqual(pattern['datasets']['2GB']['queries'], [])
        self.assertTrue(pattern['datasets']['2GB']['available'])
        self.assertFalse(pattern['datasets']['10GB']['available'])
        page, figures = render(result)
        self.assertIn('Pending', page)
        self.assertIn('scans-aggregates', figures)

    def test_svg_has_shared_zero_based_scale_and_escaped_labels(self):
        svg = chart('Rows <test>', [('a & b', [1, 2, None, 4])])
        root = ET.fromstring(svg)
        self.assertEqual(root.tag, '{http://www.w3.org/2000/svg}svg')
        self.assertIn('a &amp; b', svg)
        self.assertIn('Pending', svg)
        self.assertIn('>0</text>', svg)
        self.assertIn('width="152.00"', svg)
        self.assertIn('width="608.00"', svg)

    def test_all_generated_figures_are_valid_svg_with_empty_inputs(self):
        _, figures = render(overall_analysis(payloads(), []))
        for svg in figures.values():
            ET.fromstring(svg)
