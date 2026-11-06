"""Validate raw runs and export descriptive tables; no plotting dependencies needed."""
import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
import math
from pathlib import Path
import re
import statistics

SCENARIOS = ('baseline', 'all_dimensions')


def read_csv(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


def save_csv(path, rows):
    with Path(path).open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def rendered_sets(raw):
    """Compare rendered rows without order; preserve result boundaries and duplicates."""
    result, lines = [], []
    for line in raw.decode('utf-8').splitlines():
        if line == 'Warning: Null value is eliminated by an aggregate or other SET operation.':
            continue
        if re.fullmatch(r'\(\d+ rows? affected\)', line.strip()):
            result.append((line.strip(), Counter(lines)))
            lines = []
        else:
            lines.append(line)
    if lines:
        result.append(('tail', Counter(lines)))
    return result


def analyze(folder):
    folder = Path(folder)
    if (folder / 'STATUS').read_text().strip() != 'COMPLETE':
        raise ValueError('Incomplete/failed run: ' + str(folder))
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    if manifest['protocol'] != 'dimension_indexes_v2' or manifest['scenarios'] != list(SCENARIOS):
        raise ValueError('Unexpected experiment protocol/scenarios')
    if sorted(manifest['scenario_order']) != sorted(SCENARIOS):
        raise ValueError('Invalid scenario order')
    repeats = manifest['passes']
    if repeats < 2 or not manifest['queries']:
        raise ValueError('Need queries and at least two measured passes')
    for name, digest in manifest['queries'].items():
        if not re.fullmatch(r'[A-Za-z0-9_-]+\.sql', name):
            raise ValueError('Invalid query filename')
        if hashlib.sha256((folder / 'source_queries' / name).read_bytes()).hexdigest() != digest:
            raise ValueError('Source query changed: ' + name)
    for name in ('run.py', 'indexes.sql', 'analyze.py'):
        if hashlib.sha256((folder / name).read_bytes()).hexdigest() != manifest['files'][name]:
            raise ValueError('Runner snapshot changed: ' + name)
    queries = [Path(n).stem for n in manifest['queries']]
    setup_rows = read_csv(folder / 'setup.csv')
    setup = {r['scenario']: r for r in setup_rows}
    if len(setup_rows) != len(SCENARIOS) or set(setup) != set(SCENARIOS):
        raise ValueError('Missing or duplicate scenario storage records')
    if float(setup['baseline']['added_kb']) != 0:
        raise ValueError('Baseline has extra dimension index storage')
    baseline_kb = sum(int(r['reserved_kb']) for r in read_csv(folder / 'initial_indexes.csv'))
    times, totals, hashes = {}, {}, {}
    for scenario in SCENARIOS:
        rows = read_csv(folder / scenario / 'timings.csv')
        expected = {(q, p) for q in queries for p in range(1, repeats + 1)}
        seen, per_query, per_pass = set(), defaultdict(list), defaultdict(float)
        for row in rows:
            key = row['query_id'], int(row['pass_number'])
            value = float(row['duration_ms'])
            if key not in expected or key in seen or not math.isfinite(value) or value <= 0:
                raise ValueError('Invalid/duplicate timing in ' + scenario)
            seen.add(key)
            per_query[key[0]].append(value)
            per_pass[key[1]] += value / 1000
        if seen != expected:
            raise ValueError('Missing measurements in ' + scenario)
        times[scenario], totals[scenario] = per_query, per_pass
        for q in queries:
            hashes[scenario, q] = hashlib.sha256((folder / scenario / 'outputs' / (q + '.txt')).read_bytes()).hexdigest()
    recorded = read_csv(folder / 'output_hashes.csv')
    if len(recorded) != len(hashes) or len({(r['scenario'], r['query_id']) for r in recorded}) != len(hashes):
        raise ValueError('Missing/duplicate output checks')
    if any(hashes.get((r['scenario'], r['query_id'])) != r['sha256'] for r in recorded):
        raise ValueError('Saved output changed')
    usage = read_csv(folder / 'estimated_index_usage.csv')
    indexes = [r for r in read_csv(folder / 'all_dimensions' / 'indexes.csv')
               if r['index'].startswith('DIMEXP_')]
    expected_indexes = set(re.findall(r'CREATE NONCLUSTERED INDEX (DIMEXP_\w+)',
                                     (folder / 'indexes.sql').read_text()))
    if len(indexes) != 9 or {r['index'] for r in indexes} != expected_indexes:
        raise ValueError('Expected all nine dimension indexes in the inventory')
    if any(r['scenario'] != 'all_dimensions' or r['query_id'] not in queries or
           r['index'] not in expected_indexes for r in usage):
        raise ValueError('Unexpected estimated index reference')
    index_review = []
    for index in indexes:
        selected = sorted({r['query_id'] for r in usage if r['index'] == index['index']})
        index_review.append(dict(index=index['index'], table=index['table'], columns=index['columns'],
                                 added_mib=float(index['reserved_kb'])/1024,
                                 estimated_query_count=len(selected), queries=';'.join(selected),
                                 selection='selected' if selected else 'not_selected',
                                 review_action='review_benefit' if selected else 'candidate_for_removal'))
    output_checks, query_rows, workload_rows, summary = [], [], [], []
    for scenario in SCENARIOS:
        reviews = 0
        for q in queries:
            status = 'reference' if scenario == 'baseline' else 'raw_match'
            if hashes[scenario, q] != hashes['baseline', q]:
                a = (folder / 'baseline' / 'outputs' / (q + '.txt')).read_bytes()
                b = (folder / scenario / 'outputs' / (q + '.txt')).read_bytes()
                status = 'same_rendered_rows_order_or_null_warning' if rendered_sets(a) == rendered_sets(b) else 'REVIEW_REQUIRED'
            reviews += status == 'REVIEW_REQUIRED'
            output_checks.append(dict(scenario=scenario, query_id=q, status=status))
            mean = statistics.mean(times[scenario][q])
            base = statistics.mean(times['baseline'][q])
            query_rows.append(dict(scenario=scenario, query_id=q, mean_ms=mean,
                                   sd_ms=statistics.stdev(times[scenario][q]), baseline_ms=base,
                                   delta_s=(mean - base) / 1000, change_pct=100 * (mean / base - 1)))
        for p, total in sorted(totals[scenario].items()):
            workload_rows.append(dict(scenario=scenario, pass_number=p, total_s=total))
        mean = statistics.mean(totals[scenario].values())
        base = statistics.mean(totals['baseline'].values())
        added_kb = float(setup[scenario]['added_kb'])
        summary.append(dict(scenario=scenario, queries=len(queries), passes=repeats,
                            mean_s=mean, sd_s=statistics.stdev(totals[scenario].values()),
                            delta_s=mean-base, change_pct=100*(mean/base-1), speedup=base/mean,
                            added_mib=added_kb/1024,
                            added_storage_pct=100*added_kb/baseline_kb if baseline_kb else 0,
                            setup_s=float(setup[scenario]['setup_ms'])/1000, output_reviews=reviews))
    exports = folder / 'analysis'
    exports.mkdir(exist_ok=True)
    for name, rows in [('scenario_summary', summary), ('query_comparisons', query_rows),
                       ('workload_passes', workload_rows), ('output_checks', output_checks),
                       ('index_review', index_review)]:
        save_csv(exports / (name + '.csv'), rows)
    return dict(folder=str(folder), manifest=manifest, summary=summary, queries=query_rows,
                passes=workload_rows, output_checks=output_checks,
                usage=usage, index_review=index_review)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    args = parser.parse_args()
    result = analyze(args.run)
    for row in result['summary']:
        print(f"{row['scenario']:24} {row['mean_s']:9.3f} s  {row['change_pct']:+7.2f}%  "
              f"{row['added_mib']:8.2f} MiB  reviews={row['output_reviews']}")
