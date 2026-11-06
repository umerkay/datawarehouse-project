import csv
import re
import tempfile
import unittest
from pathlib import Path
from query_analyzer.core.analysis import analyze
from query_analyzer.core.sources import read_indexes, load_sources
from query_analyzer.web.plan_views import plan_tree, index_review
from query_analyzer.web.dashboard import render
from query_analyzer.web.experiments import render as experiments
from query_analyzer.tests.helpers import PLAN


class PlanViewsTests(unittest.TestCase):
    def test_ordering_and_grouping_are_local_to_operator(self):
        child = '<RelOp NodeId="1" PhysicalOp="Hash Match" LogicalOp="Aggregate" EstimateRows="3"><Hash><HashKeysBuild><ColumnReference Schema="[dbo]" Table="[t]" Column="a"/></HashKeysBuild></Hash></RelOp>'
        xml = PLAN.replace('<IndexScan><Object', '<IndexScan>' + child + '<Object')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'q.xml'
            path.write_text(xml)
            ops = {op['node']: op for op in analyze(path)['statements'][0]['operators']}
        self.assertEqual(ops['0']['grouping'], [])
        self.assertEqual(ops['1']['grouping'][0]['Column'], 'a')

    def test_tree_keeps_branch_order_and_predicate_ownership(self):
        child = '<RelOp NodeId="1" PhysicalOp="Index Seek" EstimateRows="3"><IndexScan><SeekPredicates><ScalarOperator ScalarString="[x] &lt; 5"/></SeekPredicates></IndexScan></RelOp>'
        xml = PLAN.replace('<IndexScan><Object', '<IndexScan><Predicate><ScalarOperator ScalarString="[parent] = 1"/></Predicate>' + child + '<Object')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'q.xml'
            path.write_text(xml)
            stmt = analyze(path)['statements'][0]
        parent = next(o for o in stmt['operators'] if o['node'] == '0')
        self.assertEqual(parent['children'], ['1'])
        self.assertEqual(parent['predicates'], [dict(role='Predicate', expression='[parent] = 1')])
        page = plan_tree(stmt)
        self.assertLess(page.index('Node 0:'), page.index('Node 1:'))
        self.assertIn('[x] &lt; 5', page)

    def test_inventory_coverage_and_filtered_index(self):
        candidate = dict(schema='[dbo]', table='[t]', columns={'EQUALITY': ['[a]'], 'INCLUDE': ['[b]']})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'indexes.csv'
            with path.open('w', newline='') as stream:
                writer = csv.writer(stream)
                writer.writerow(['schema', 'table', 'index_name', 'index_type', 'filter_definition', 'column_name', 'key_ordinal', 'is_included_column'])
                writer.writerow(['[dbo]', '[t]', 'ix', 'NONCLUSTERED', '', '[a]', 1, 0])
                writer.writerow(['[dbo]', '[t]', 'ix', 'NONCLUSTERED', '', '[b]', 0, 1])
            inventory = read_indexes(path)
        self.assertIn('Potential column coverage', index_review([candidate], inventory))
        inventory[0]['filter'] = '[a] > 2'
        self.assertNotIn('Potential column coverage', index_review([candidate], inventory))
        self.assertIn('inventory unavailable', index_review([candidate], None))
        self.assertIn('No enabled index', index_review([candidate], []))

    def test_estimated_only_hides_cardinality_filter(self):
        xml = re.sub(r'<RunTimeInformation>.*?</RunTimeInformation>', '', PLAN, flags=re.S)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'q.xml'
            path.write_text(xml)
            query = dict(analyze(path), average_ms=None)
        page = render(dict(queries=[query], errors=[], timing_source=None), '')
        self.assertNotIn('value="cardinality"', page)

    def test_optional_indexed_inputs_and_pending_states(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, xml in [('baseline', PLAN), ('indexed', PLAN.replace('Clustered Index Scan', 'Index Seek'))]:
                (root / name).mkdir()
                (root / name / 'query_001.xml').write_text(xml)
            (root / 'config.json').write_text(json.dumps({'1GB': {'plans': 'baseline', 'indexed_plans': 'indexed'}}))
            sources = load_sources(root / 'config.json')
            page = experiments(sources)
        self.assertIn('Structure: changed', page)
        self.assertIn('Index Seek: 0 → 1', page)
        self.assertIn('Paired timing measurements unavailable', page)
        self.assertIn('Indexed captures pending', page)
