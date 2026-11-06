"""Exercise analysis with small synthetic runs; never presented as SQL measurements."""
import csv
import hashlib
import importlib.util
import json
import re
from pathlib import Path
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('dimension_analysis', HERE / 'analyze.py')
analysis = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analysis)


def fixture(folder):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'STATUS').write_text('COMPLETE\n')
    source = folder / 'source_queries'
    source.mkdir()
    queries = {}
    for name in ['q1.sql', 'q2.sql']:
        data = b'SELECT 1;'
        (source / name).write_bytes(data)
        queries[name] = hashlib.sha256(data).hexdigest()
    files = {}
    for name in ['run.py', 'indexes.sql', 'analyze.py']:
        data = (HERE / name).read_bytes()
        (folder / name).write_bytes(data)
        files[name] = hashlib.sha256(data).hexdigest()
    manifest = dict(protocol='dimension_indexes_v2', scenarios=list(analysis.SCENARIOS),
                    scenario_order=list(reversed(analysis.SCENARIOS)), queries=queries,
                    passes=2, files=files, scale='1GB', query_scale='1GB', seed=1,
                    source_database='fixture_only', experiment_database='fixture_dimexp_test')
    (folder / 'manifest.json').write_text(json.dumps(manifest))
    setup, hashes = [], []
    for scenario in analysis.SCENARIOS:
        d = folder / scenario
        (d / 'outputs').mkdir(parents=True)
        rows = []
        factor = 1 if scenario == 'baseline' else 0.5
        for repeat in [1, 2]:
            for q in ['q1', 'q2']:
                rows.append(dict(pass_number=repeat, query_id=q, duration_ms=100*repeat*factor))
        analysis.save_csv(d / 'timings.csv', rows)
        for q in ['q1', 'q2']:
            raw = b'value\n-----\n1\n2\n(2 rows affected)\n'
            (d / 'outputs' / (q + '.txt')).write_bytes(raw)
            hashes.append(dict(scenario=scenario, query_id=q, sha256=hashlib.sha256(raw).hexdigest()))
        setup.append(dict(scenario=scenario, setup_ms=10, added_kb=0 if scenario == 'baseline' else 1024))
    analysis.save_csv(folder / 'setup.csv', setup)
    analysis.save_csv(folder / 'output_hashes.csv', hashes)
    analysis.save_csv(folder / 'initial_indexes.csv', [dict(reserved_kb=10240)])
    indexes = re.findall(r'CREATE NONCLUSTERED INDEX (DIMEXP_\w+) ON dbo\.(\w+)\((\w+)\)', (HERE / 'indexes.sql').read_text())
    analysis.save_csv(folder / 'all_dimensions/indexes.csv', [dict(index=n, table=t, columns=c+':1:0', reserved_kb=64) for n,t,c in indexes])
    (folder / 'estimated_index_usage.csv').write_text('scenario,query_id,table,index\n')
    return folder


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.folder = fixture(Path(self.temporary.name) / 'run')

    def tearDown(self):
        self.temporary.cleanup()

    def test_workload_statistics_use_complete_passes(self):
        result = analysis.analyze(self.folder)
        baseline, combined = result['summary'][:2]
        self.assertAlmostEqual(baseline['mean_s'], 0.3)
        self.assertAlmostEqual(baseline['sd_s'], 0.1414213562373095)
        self.assertAlmostEqual(combined['change_pct'], -50)
        self.assertAlmostEqual(combined['speedup'], 2)
        self.assertAlmostEqual(combined['added_mib'], 1)
        self.assertEqual(sum(r['output_reviews'] for r in result['summary']), 0)

    def test_incomplete_measurements_rejected(self):
        p = self.folder / 'all_dimensions/timings.csv'
        rows = analysis.read_csv(p)
        analysis.save_csv(p, rows[:-1])
        with self.assertRaisesRegex(ValueError, 'Missing measurements'):
            analysis.analyze(self.folder)

    def test_duplicate_measurement_rejected(self):
        p = self.folder / 'all_dimensions/timings.csv'
        rows = analysis.read_csv(p)
        analysis.save_csv(p, rows + rows[:1])
        with self.assertRaisesRegex(ValueError, 'duplicate timing'):
            analysis.analyze(self.folder)

    def test_tampered_source_rejected(self):
        (self.folder / 'source_queries/q1.sql').write_text('SELECT 2;')
        with self.assertRaisesRegex(ValueError, 'Source query changed'):
            analysis.analyze(self.folder)

    def test_tampered_output_rejected(self):
        (self.folder / 'all_dimensions/outputs/q1.txt').write_text('different')
        with self.assertRaisesRegex(ValueError, 'Saved output changed'):
            analysis.analyze(self.folder)

    def test_changed_values_flagged_even_when_hash_matches_saved_file(self):
        p = self.folder / 'all_dimensions/outputs/q1.txt'
        p.write_bytes(b'value\n-----\n9\n2\n(2 rows affected)\n')
        rows = analysis.read_csv(self.folder / 'output_hashes.csv')
        for row in rows:
            if row['scenario'] == 'all_dimensions' and row['query_id'] == 'q1':
                row['sha256'] = hashlib.sha256(p.read_bytes()).hexdigest()
        analysis.save_csv(self.folder / 'output_hashes.csv', rows)
        result = analysis.analyze(self.folder)
        self.assertEqual(result['summary'][1]['output_reviews'], 1)

    def test_row_order_is_distinguished_from_value_changes(self):
        a = b'v\n-\n1\n2\n(2 rows affected)\n'
        b = b'v\n-\n2\n1\n(2 rows affected)\n'
        self.assertEqual(analysis.rendered_sets(a), analysis.rendered_sets(b))
        self.assertNotEqual(analysis.rendered_sets(a), analysis.rendered_sets(a.replace(b'2\n', b'1\n', 1)))

    def test_failed_run_rejected(self):
        (self.folder / 'STATUS').write_text('FAILED\n')
        with self.assertRaisesRegex(ValueError, 'Incomplete/failed'):
            analysis.analyze(self.folder)

    def test_index_review_keeps_unused_indexes_and_counts_distinct_queries(self):
        (self.folder / 'estimated_index_usage.csv').write_text(
            'scenario,query_id,table,index\n'
            'all_dimensions,q1,date_dim,DIMEXP_date_year\n'
            'all_dimensions,q1,date_dim,DIMEXP_date_year\n')
        result = analysis.analyze(self.folder)
        review = {r['index']: r for r in result['index_review']}
        self.assertEqual(len(review), 9)
        self.assertEqual(review['DIMEXP_date_year']['estimated_query_count'], 1)
        self.assertEqual(review['DIMEXP_date_year']['queries'], 'q1')
        self.assertEqual(review['DIMEXP_date_year']['selection'], 'selected')
        self.assertEqual(sum(r['review_action'] == 'candidate_for_removal' for r in review.values()), 8)
        self.assertTrue((self.folder / 'analysis/index_review.csv').is_file())


if __name__ == '__main__':
    unittest.main()
