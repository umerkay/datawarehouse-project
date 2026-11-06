"""Exercise orchestration with a fake SQL transport, without Docker or a real database."""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('dimension_runner', HERE / 'run.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class FakeSql:
    def __init__(self, *_):
        self.active = ()
        self.calls = []

    def execute(self, database, text, **kwargs):
        self.calls.append((database, text))
        if 'DECLARE @scenario sysname' in text:
            scenario = re.search(r"DECLARE @scenario sysname = N'([^']+)'", text)[1]
            self.active = sum(runner.INDEXES.values(), ()) if scenario == 'all_dimensions' else runner.INDEXES.get(scenario, ())
        if text == runner.INVENTORY:
            lines = ['dbo|store_sales|IMCCI_store_sales|CLUSTERED COLUMNSTORE|1024|ss_item_sk:0:0']
            lines += [f'dbo|customer|{name}|NONCLUSTERED|64|column:1:0' for name in self.active]
            return ('\n'.join(lines) + '\n').encode()
        if text.startswith('SET SHOWPLAN_XML ON;'):
            objects = ''.join(f'<Object Table="[customer]" Index="[{i}]"/>' for i in self.active)
            return ('<ShowPlanXML xmlns="http://schemas.microsoft.com/sqlserver/2004/07/showplan">'
                    + objects + '</ShowPlanXML>').encode()
        return b'fixture-output\n'


class RunnerTests(unittest.TestCase):
    def test_complete_run_uses_private_copy_and_saves_reanalyzable_measurements(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            queries = root / 'queries'
            queries.mkdir()
            (queries / 'queries.lst').write_text('q1.sql\nq2.sql\n')
            (queries / 'q1.sql').write_text('SELECT 1;')
            (queries / 'q2.sql').write_text('SELECT 2;')
            args = argparse.Namespace(database='source_cci', scale='1GB', query_scale='1GB',
                                      queries=queries, seed=42, dry_run=False,
                                      container='fixture', timeout=10, passes=2)
            transport = FakeSql()
            with patch.object(runner, 'ROOT', root), patch.object(runner, 'Sql', return_value=transport), \
                    patch.dict(sys.modules, {}), patch.object(sys, 'path', [str(HERE)] + sys.path):
                runner.run(args)
            output = next((root / 'benchmark_results/dimension_indexes').iterdir())
            self.assertEqual((output / 'STATUS').read_text().strip(), 'COMPLETE')
            manifest = json.loads((output / 'manifest.json').read_text())
            self.assertNotEqual(manifest['scenario_order'], list(runner.SCENARIOS))
            for database, sql in transport.calls:
                if 'DROP INDEX' in sql or sql == runner.FULLSCAN or sql in ['SELECT 1;', 'SELECT 2;']:
                    self.assertIn('_dimexp_', database)
            self.assertTrue((output / 'analysis/scenario_summary.csv').is_file())
            rows = (output / 'all_dimensions/timings.csv').read_text().splitlines()
            self.assertEqual(len(rows), 5)  # Header + 2 queries x 2 measured passes; no warm-up.

    def test_dry_run_never_connects_or_creates_results(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'queries.lst').write_text('q.sql\n')
            (root / 'q.sql').write_text('SELECT 1;')
            args = argparse.Namespace(database='source_cci', scale='1GB', query_scale='1GB',
                                      queries=root, seed=42, dry_run=True)
            with patch.object(runner, 'Sql', side_effect=AssertionError('Must not connect')):
                runner.run(args)
            self.assertEqual(sorted(p.name for p in root.iterdir()), ['q.sql', 'queries.lst'])


if __name__ == '__main__':
    unittest.main()
