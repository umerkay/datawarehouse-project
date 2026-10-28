"""Validate completed experiment phases and produce descriptive statistics/figures."""
import argparse
from collections import Counter
import csv
import gzip
import hashlib
import html
import json
import math
import os
from pathlib import Path
import re
import statistics

NULL_WARNING = 'Warning: Null value is eliminated by an aggregate or other SET operation.'


def csv_rows(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


def rendered_sets(raw, ignore_null_warning=False):
    """Preserve result-set boundaries and duplicate rendered lines."""
    sets, lines = [], []
    for line in raw.decode('utf-8', errors='strict').splitlines():
        if ignore_null_warning and line == NULL_WARNING:
            continue
        if re.fullmatch(r'\(\d+ rows? affected\)', line.strip()):
            sets.append((line.strip(), Counter(lines)))
            lines = []
        else:
            lines.append(line)
    if lines:
        sets.append(('tail', Counter(lines)))
    return sets


def output_check(reference, candidate):
    a, b = gzip.decompress(Path(reference).read_bytes()), gzip.decompress(Path(candidate).read_bytes())
    if a == b:
        return 'identical_raw_output'
    if rendered_sets(a) == rendered_sets(b):
        return 'same_rendered_rows_different_order'
    # Preserve this diagnostic difference explicitly; never discard arbitrary
    # warnings or error lines and never apply numeric rounding/tolerances.
    if rendered_sets(a, True) == rendered_sets(b, True):
        return 'same_rendered_rows_null_warning_difference'
    return 'review_values_or_format'


def geometric_mean(values):
    return math.exp(statistics.mean(math.log(v) for v in values)) if values else None


def analyze_run(folder):
    folder = Path(folder)
    manifest = dict(line.split('=', 1) for line in (folder / 'manifest.txt').read_text().splitlines() if '=' in line)
    selected = [(line[:-4] if line.endswith('.sql') else line) for line in (folder / 'queries.lst').read_text().splitlines()]
    if not selected or len(selected) != len(set(selected)):
        raise ValueError('Invalid query selection')
    workload = {r['scenario']: r for r in csv_rows(folder / 'workload.csv')}
    phases, queries, pending = [], {}, []
    for scenario in manifest['scenarios'].split():
        path = folder / scenario
        if scenario not in workload or not (path / 'summary.csv').exists():
            pending.append(scenario)
            continue
        samples = {q: {} for q in selected}
        for row in csv_rows(path / 'timings.csv'):
            q, n, duration = row['query_id'], int(row['run']), float(row['duration_ms'])
            if q not in samples or n not in range(1, 6) or n in samples[q] or duration < 0 or not math.isfinite(duration):
                raise ValueError(f'{scenario}: invalid/duplicate measured execution')
            samples[q][n] = duration
        summary = {r['query_id']: r for r in csv_rows(path / 'summary.csv')}
        checks = {r['query_id']: r for r in csv_rows(path / 'result_checks.csv')}
        if set(summary) != set(selected):
            raise ValueError(f'{scenario}: summary coverage mismatch')
        stats = {}
        for q, numbered in samples.items():
            if set(numbered) != set(range(1, 6)):
                raise ValueError(f'{scenario}/{q}: requires five distinct measurements')
            values = [numbered[n] for n in range(1,6)]
            mean, sd = statistics.mean(values), statistics.stdev(values)
            recorded = summary[q]
            raw = gzip.decompress((path/'results'/(q+'.txt.gz')).read_bytes())
            if q not in checks or hashlib.sha256(raw).hexdigest() != checks[q]['sha256']:
                raise ValueError(f'{scenario}/{q}: output hash mismatch')
            if int(recorded['runs']) != 5 or abs(float(recorded['average_ms']) - mean) > .051 or float(recorded['total_ms']) != sum(values) or float(recorded['min_ms']) != min(values) or float(recorded['max_ms']) != max(values):
                raise ValueError(f'{scenario}/{q}: summary does not match raw measurements')
            status = 'reference' if scenario == 'baseline' else output_check(folder / 'baseline/results' / (q + '.txt.gz'), path / 'results' / (q + '.txt.gz'))
            stats[q] = dict(query_id=q, average_ms=mean, median_ms=statistics.median(values), sd_ms=sd,
                            min_ms=min(values), max_ms=max(values), cv_percent=100 * sd / mean if mean else None,
                            output_check=status, null_warning_count=raw.decode('utf-8').splitlines().count(NULL_WARNING), samples_ms=values)
        if abs(float(workload[scenario]['sum_average_ms']) - sum(float(summary[q]['average_ms']) for q in selected)) > .11:
            raise ValueError(f'{scenario}: workload total mismatch')
        queries[scenario] = stats
        pass_totals = [sum(samples[q][n] for q in selected)/1000 for n in range(1,6)]
        phases.append(dict(scenario=scenario, queries=len(stats), sum_average_seconds=sum(s['average_ms'] for s in stats.values()) / 1000,
                           sd_pass_total_seconds=statistics.stdev(pass_totals), median_pass_total_seconds=statistics.median(pass_totals),
                           pass_totals_seconds=pass_totals,
                           index_mib=float(workload[scenario]['index_reserved_kb']) / 1024,
                           index_setup_seconds=float(workload[scenario]['setup_ms']) / 1000,
                           output_checks=dict(Counter(s['output_check'] for s in stats.values()))))
    if 'baseline' not in queries:
        raise ValueError('No complete validated baseline')
    baseline = queries['baseline']
    base_total = next(p['sum_average_seconds'] for p in phases if p['scenario'] == 'baseline')
    comparisons = []
    for phase in phases:
        name = phase['scenario']
        ratios = []
        for q, stat in queries[name].items():
            original = baseline[q]['average_ms']
            ratio = original / stat['average_ms'] if stat['average_ms'] > 0 and original > 0 else None
            stat.update(baseline_ms=original, change_ms=stat['average_ms'] - original,
                        change_percent=100 * (stat['average_ms'] / original - 1) if original else None, speedup=ratio)
            if ratio is not None:
                ratios.append(ratio)
            comparisons.append(dict(scenario=name, **{k: v for k, v in stat.items() if k != 'samples_ms'}))
        phase.update(change_percent=100 * (phase['sum_average_seconds'] / base_total - 1),
                     workload_speedup=base_total / phase['sum_average_seconds'], geometric_mean_speedup=geometric_mean(ratios),
                     faster_over_5pct=sum(s['change_percent'] is not None and s['change_percent'] < -5 for s in queries[name].values()),
                     slower_over_5pct=sum(s['change_percent'] is not None and s['change_percent'] > 5 for s in queries[name].values()))
    pairs = []
    for candidate, reference in [('date_shared', 'date_keys'), ('date_priority', 'date_keys'), ('combined', 'date_shared'), ('combined', 'columnstore'), ('columnstore_date_keys', 'columnstore'), ('columnstore_date_keys', 'date_keys'), ('columnstore_date_keys', 'combined')]:
        if candidate in queries and reference in queries:
            before = sum(s['average_ms'] for s in queries[reference].values())
            after = sum(s['average_ms'] for s in queries[candidate].values())
            pairs.append(dict(candidate=candidate, reference=reference, change_percent=100*(after/before-1), speedup=before/after))
    return dict(folder=str(folder.resolve()), manifest=manifest, complete=(folder/'STATUS').read_text().strip() == 'COMPLETE' and not pending,
                pending=pending, resumed=(folder/'resume_events.log').exists(), phases=phases, queries=queries,
                comparisons=comparisons, incremental_comparisons=pairs)
