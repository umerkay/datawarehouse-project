import json
import re
from pathlib import Path
import tempfile
import unittest

from tools.query_analyzer.core.analysis import analyze
from tools.query_analyzer.app.cli import main
from tools.query_analyzer.tests.helpers import PLAN


class AnalysisTests(unittest.TestCase):
    def test_actual_parallel_cardinality_and_missing_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'query_001.sqlplan'
            path.write_text(PLAN, encoding='utf-16')
            result = analyze(path)
            stmt = result['statements'][0]
            self.assertTrue(stmt['actual'])
            self.assertIsNone(stmt['operators'][0]['cardinality_ratio'])
            self.assertTrue(stmt['operators'][0]['parallel_counters'])
            self.assertEqual(stmt['missing_indexes'][0]['columns']['EQUALITY'], ['[item]'])
            self.assertTrue(any('SpillToTempDb' in f for f in stmt['findings']))

    def test_serial_repeated_operator_cardinality(self):
        serial = re.sub(r'<RunTimeCountersPerThread Thread="1"[^>]*/>', '', PLAN)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'serial.xml'
            path.write_text(serial)
            operator = analyze(path)['statements'][0]['operators'][0]
            self.assertEqual(operator['cardinality_ratio'], 400)

    def test_sqlcmd_multiple_documents(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'query.xml'
            path.write_text('header\n-----\n' + PLAN + '\n(1 row)\n' + PLAN)
            self.assertEqual(len(analyze(path)['statements']), 2)

    def test_sqlcmd_wrap_inside_xml_token(self):
        long_plan = PLAN.replace('SELECT &lt;test&gt;', 'x' * 65500).replace('\n', '')
        wrapped = '\n'.join(long_plan[i:i+65535] for i in range(0, len(long_plan), 65535))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'wrapped.xml'
            path.write_text(wrapped)
            self.assertEqual(analyze(path)['statements'][0]['sql'], 'x' * 65500)

    def test_dashboard_embeds_sql_safely_and_collapses_details(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plans = root / 'plans'
            plans.mkdir()
            (plans / 'query_001.xml').write_text(PLAN.replace('SELECT &lt;test&gt;', '&lt;/script&gt;&lt;script&gt;alert(1)&lt;/script&gt;'))
            out = root / 'out'
            config = root / 'config.json'
            config.write_text(json.dumps({'1GB': {'plans': 'plans'}}))
            self.assertEqual(main(['build', '--config', str(config), '--output', str(out)]), 0)
            page = (out / 'report.html').read_text(encoding='utf-8')
            self.assertNotIn('</script><script>alert(1)', page)
            self.assertIn('&lt;/script&gt;&lt;script&gt;alert(1)&lt;/script&gt;', page)
            self.assertIn('<details class="query-detail"', page)
            self.assertIn('Shared index candidates', page)
            self.assertIn('<html lang="en">', page)

    def test_dashboard_has_data_without_javascript(self):
        from tools.query_analyzer.web.dashboard import render
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'query_001.xml'
            path.write_text(PLAN)
            q1 = dict(analyze(path), average_ms=90)
            q2 = dict(q1, query_id='query_002', average_ms=10)
            page = render(dict(queries=[q1, q2], errors=[], timing_source='summary.csv'), '')
            static = re.sub(r'<script>.*?</script>', '', page, flags=re.S)
            self.assertIn('<strong>2</strong>', static)
            self.assertIn('90.0 ms', static)
            self.assertIn('100.0%', static)
            self.assertEqual(static.count('data-search='), 2)
            self.assertEqual(static.count('class="barrow"'), 7)
            self.assertIn('<b>2</b>', static)  # one shared candidate, two distinct queries
            self.assertIn('Engine warning', static)
            self.assertNotIn('<!--__', page)

    def test_dashboard_empty_and_unmeasured_states(self):
        from tools.query_analyzer.web.dashboard import render
        page = render(dict(queries=[], errors=[{'error': 'invalid XML'}], timing_source=None), '')
        self.assertIn('No valid plans.', page)
        self.assertIn('invalid XML', page)
        self.assertIn('No measured timings.', page)

    def test_build_keeps_dataset_reports_and_payloads_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for scale in ('1GB', '2GB'):
                plans = root / scale
                plans.mkdir()
                text = PLAN if scale == '1GB' else PLAN.replace('SELECT &lt;test&gt;', 'SELECT 42')
                (plans / 'query_001.xml').write_text(text)
            config = root / 'config.json'
            config.write_text(json.dumps({scale: {'plans': scale} for scale in ('1GB', '2GB')}))
            output = root / 'output'
            self.assertEqual(main(['build', '--config', str(config), '--output', str(output)]), 0)
            first = json.loads((output / 'analysis.json').read_text(encoding='utf-8'))
            second = json.loads((output / 'analysis_2GB.json').read_text(encoding='utf-8'))
            self.assertEqual(first['scale'], '1GB')
            self.assertEqual(second['scale'], '2GB')
            self.assertNotEqual(first['queries'][0]['statements'][0]['sql'],
                                second['queries'][0]['statements'][0]['sql'])
            page = (output / 'report_2GB.html').read_text(encoding='utf-8')
            self.assertIn('Optimization radar - 2GB', page)
            self.assertIn('href="report_5GB.html"', page)

    def test_evidence_groups_repeated_operations_and_hides_raw_messages(self):
        from tools.query_analyzer.web.dashboard import evidence
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'query_015.xml'
            path.write_text(PLAN)
            query = dict(analyze(path), average_ms=100)
            stmt = query['statements'][0]
            original = stmt['operators'][0]
            stmt['operators'] = [dict(original, node=str(i)) for i in range(3)] + [
                dict(original, node='4', physical='Hash Match', objects=[]),
                dict(original, node='5', physical='Hash Match', objects=[])]
            output = evidence(query)
            summary = output.split('<summary>Advanced:')[0]
            self.assertIn('[sales]: 3 scan operators', summary)
            self.assertIn('2 hash operations', summary)
            without_tree = re.sub(r'<details class="plan-tree">.*?</ul></details>', '', summary, flags=re.S)
            self.assertNotIn('Node 0', without_tree)
            self.assertIn('Execution plan tree and predicates', output)
            self.assertIn('<summary>Advanced: all 5 operators', output)
            self.assertIn('<summary>Query SQL</summary>', output)
            self.assertNotIn('consider an indexed view', summary)

    def test_report_timing_join_escape_and_partial_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plans = root / 'plans'
            plans.mkdir()
            (plans / 'query_001.xml').write_text(PLAN)
            (plans / 'broken.xml').write_text('<ShowPlanXML>')
            (root / 'summary.csv').write_text('query_id,average_ms\nquery_001,125.5\n')
            out = root / 'out'
            config = root / 'config.json'
            config.write_text(json.dumps({'1GB': {'plans': 'plans', 'summary': 'summary.csv'}}))
            self.assertEqual(main(['build', '--config', str(config), '--output', str(out)]), 1)
            payload = json.loads((out / 'analysis.json').read_text(encoding='utf-8'))
            self.assertEqual(payload['queries'][0]['average_ms'], 125.5)
            self.assertEqual(len(payload['errors']), 1)
            self.assertIn('SELECT &lt;test&gt;', (out / 'report.html').read_text(encoding='utf-8'))

    def test_estimated_plan_has_no_actual_rows(self):
        estimated = re.sub(r'<RunTimeInformation>.*?</RunTimeInformation>', '', PLAN, flags=re.S)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'estimated.xml'
            path.write_text(estimated)
            stmt = analyze(path)['statements'][0]
            self.assertFalse(stmt['actual'])
            self.assertIsNone(stmt['operators'][0]['actual_rows'])

    def test_nonfinite_estimates_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'invalid.xml'
            path.write_text(PLAN.replace('EstimateRows="20000"','EstimateRows="NaN"'))
            with self.assertRaises(ValueError):
                analyze(path)

    def test_index_candidates_with_different_impacts_are_grouped_once(self):
        from tools.query_analyzer.web.dashboard import evidence
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'query_001.xml'
            path.write_text(PLAN)
            query = dict(analyze(path), average_ms=None)
            indexes = query['statements'][0]['missing_indexes']
            indexes.append(dict(indexes[0], impact=99.5))
            self.assertIn('1 distinct index candidate(s)', evidence(query))

    def test_prefixed_showplan_namespace_is_supported(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'prefixed.xml'
            text=PLAN.replace('<ShowPlanXML ', '<p:ShowPlanXML xmlns:p="http://schemas.microsoft.com/sqlserver/2004/07/showplan" ').replace('</ShowPlanXML>', '</p:ShowPlanXML>')
            path.write_text(text)
            self.assertEqual(analyze(path)['estimated_cost'],2)


if __name__ == '__main__':
    unittest.main()
