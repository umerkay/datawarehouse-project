import json
from pathlib import Path
import tempfile
import unittest

from tools.query_analyzer.core.sources import load_sources
from tools.query_analyzer.web.comparison import duration_chart, render, workload_data, operator_chart, plan_matrix
from tools.query_analyzer.core.plan_structure import structure
import xml.etree.ElementTree as ET
from tools.query_analyzer.tests.helpers import PLAN


class ComparisonTests(unittest.TestCase):
    def test_plan_map_distinguishes_topology_sql_and_missing_plans(self):
        reference = dict(shape='a', sql='sql1')
        sources = {s: dict(plans={}) for s in ('1GB', '2GB', '5GB', '10GB')}
        sources['2GB']['plans']['query_001'] = reference
        sources['5GB']['plans']['query_001'] = dict(shape='a', sql='sql2')
        sources['10GB']['plans']['query_001'] = dict(shape='b', sql='sql1')
        page = plan_matrix(sources, ['query_001'])
        self.assertIn('data-plan-states="missing reference same sql changed"', page)
        self.assertEqual(page.count('SQL differs'), 1)
        self.assertIn('class="plan-cell reference"', page)
        self.assertIn('class="plan-cell missing"', page)

    def test_operator_bars_share_absolute_scale_and_keep_missing_distinct_from_zero(self):
        sources = {s: dict(plans={}) for s in ('1GB', '2GB', '5GB', '10GB')}
        def plan(names):
            return dict(statements=[dict(operators=[dict(physical=name) for name in names])])
        sources['1GB']['plans']['query_001'] = plan(['Sort', 'Sort', 'Hash Match'])
        sources['2GB']['plans']['query_001'] = plan(['Sort'])
        page = operator_chart(sources, 'query_001')
        self.assertIn('scale-1GB" style="width:100.00%', page)
        self.assertIn('scale-2GB" style="width:50.00%', page)
        self.assertIn('scale-2GB" style="width:0.00%', page)
        self.assertEqual(page.count('No plan'), 4)
        self.assertIn('shared scale from 0 to 2', page)

    def test_workload_keeps_missing_evidence_and_deduplicates_index_candidates(self):
        candidate = dict(schema='dbo', table='sales', columns={'EQUALITY': ['id']})
        statement = dict(missing_indexes=[candidate, candidate], findings=['Warnings: spill', 'Large scan'])
        sources = {scale: dict(plans={}, timings={'query_001': 0}) for scale in ('1GB', '2GB', '5GB', '10GB')}
        sources['1GB']['plans']['query_001'] = dict(statements=[statement, statement])
        result = workload_data(sources, ['query_001'])[0]['scales']
        self.assertEqual(result['1GB'], dict(ms=0, indexes=1, warnings=2))
        self.assertEqual(result['5GB'], dict(ms=0, indexes=None, warnings=None))

    def chart_svg(self, values):
        markup = duration_chart('query_015', values)
        start, end = markup.index('<svg'), markup.index('</svg>')+6
        return ET.fromstring(markup[start:end])

    def test_duration_chart_converts_milliseconds_and_uses_numeric_x_axis(self):
        svg = self.chart_svg([1000, 2000, 5000, 10000])
        points = svg.findall('circle')
        self.assertEqual([float(p.get('data-seconds')) for p in points], [1, 2, 5, 10])
        x = [float(p.get('cx')) for p in points]
        self.assertAlmostEqual((x[2]-x[0])/(x[3]-x[0]), 4/9, places=4)
        self.assertEqual(len(svg.findall('polyline')), 1)

    def test_duration_chart_does_not_connect_across_missing_sizes(self):
        svg = self.chart_svg([1000, None, 5000, 10000])
        self.assertEqual(len(svg.findall('circle')), 3)
        segments = svg.findall('polyline')
        self.assertEqual(len(segments), 1)
        self.assertEqual(len(segments[0].get('points').split()), 2)

    def test_duration_chart_handles_zero_and_no_data(self):
        svg = self.chart_svg([0, 0, 0, 0])
        self.assertEqual(len(svg.findall('circle')), 4)
        self.assertNotIn('nan', ET.tostring(svg,encoding='unicode'))
        self.assertIn('No historical timings', duration_chart('query_001',[None]*4))

    def test_shape_ignores_row_counts_and_node_ids_but_flags_changed_sql(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp) / 'a.xml', Path(tmp) / 'b.xml'
            a.write_text(PLAN)
            b.write_text(PLAN.replace('EstimateRows="20000"', 'EstimateRows="200000"').replace('NodeId="0"', 'NodeId="45"'))
            self.assertEqual(structure(a)['shape'], structure(b)['shape'])
            b.write_text(PLAN.replace('SELECT &lt;test&gt;', 'SELECT 42'))
            self.assertEqual(structure(a)['shape'], structure(b)['shape'])
            self.assertNotEqual(structure(a)['sql'], structure(b)['sql'])

    def test_shape_detects_different_topology_with_same_operator_counts(self):
        prefix = '<ShowPlanXML xmlns="http://schemas.microsoft.com/sqlserver/2004/07/showplan"><BatchSequence><Batch><Statements><StmtSimple StatementText="SELECT 1"><QueryPlan>'
        suffix = '</QueryPlan></StmtSimple></Statements></Batch></BatchSequence></ShowPlanXML>'
        chain = '<RelOp PhysicalOp="Sort"><Sort><RelOp PhysicalOp="Hash Match"><Hash><RelOp PhysicalOp="Index Scan"/></Hash></RelOp></Sort></RelOp>'
        fork = '<RelOp PhysicalOp="Sort"><Sort><RelOp PhysicalOp="Hash Match"/><RelOp PhysicalOp="Index Scan"/></Sort></RelOp>'
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp) / 'a.xml', Path(tmp) / 'b.xml'
            a.write_text(prefix + chain + suffix)
            b.write_text(prefix + fork + suffix)
            self.assertNotEqual(structure(a)['shape'], structure(b)['shape'])

    def test_missing_plans_keep_four_scale_timings_visible_without_scripts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'plans').mkdir()
            (root / 'plans/query_015.xml').write_text(PLAN)
            (root / 'timing.csv').write_text('query_id,average_ms\nquery_015,1234\n')
            config = root / 'sources.json'
            config.write_text(json.dumps({s: {'plans': 'plans' if s == '1GB' else 'missing', 'summary': 'timing.csv'} for s in ('1GB','2GB','5GB','10GB')}))
            sources = load_sources(config)
            page = render(sources)
            self.assertEqual(page.count('No plan captured for this query and size.'), 3)
            self.assertIn('1.234 s', page)
            self.assertIn('Reference plan', page)
            self.assertIn('Plan tree', page)
            self.assertIn('id="compare-query_015"', page)
            self.assertNotIn('<!--__', page)
            self.assertIn('id="workload-panel"', page)
            payload = page.split('id="workload-data">')[1].split('</script>')[0]
            self.assertEqual(json.loads(payload)[0]['scales']['2GB']['indexes'], None)

    def test_invalid_plan_is_reported_and_does_not_hide_timing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'plans').mkdir()
            (root / 'plans/query_001.xml').write_text('invalid')
            (root / 'timing.csv').write_text('query_id,average_ms\nquery_001,1000\n')
            config = root / 'sources.json'
            config.write_text(json.dumps({'1GB': {'plans':'plans','summary':'timing.csv'}}))
            sources = load_sources(config)
            self.assertEqual(len(sources['1GB']['errors']), 1)
            page = render(sources)
            self.assertIn('Input errors', page)
            self.assertIn('1.000 s', page)

    def test_side_by_side_structure_and_sql_statuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = {}
            for scale in ('1GB', '2GB', '5GB', '10GB'):
                folder = root / scale
                folder.mkdir()
                text = PLAN
                if scale == '5GB':
                    text = text.replace('Clustered Index Scan', 'Index Seek')
                if scale == '10GB':
                    text = text.replace('SELECT &lt;test&gt;', 'SELECT 42')
                (folder / 'query_001.xml').write_text(text)
                config[scale] = {'plans': scale}
            path = root / 'config.json'
            path.write_text(json.dumps(config))
            page = render(load_sources(path))
            self.assertEqual(page.count('Same plan structure'), 2)
            self.assertEqual(page.count('Different plan structure'), 1)
            self.assertIn('Captured SQL differs', page)


if __name__ == '__main__':
    unittest.main()
