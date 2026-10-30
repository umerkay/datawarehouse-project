"""Validate raw executions and export CSV/PNG/SVG, without generating a website."""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import statistics
import xml.etree.ElementTree as ET

from validation import analyze_run

LABELS = {
    'baseline': 'Baseline', 'date_keys': 'Date-key indexes',
    'date_shared': 'Date keys + shared INCLUDE',
    'date_priority': 'Date keys + query_015 INCLUDE',
    'columnstore': 'Columnstore',
    'columnstore_date_keys': 'Columnstore + date-key indexes',
}
SCENARIOS = list(LABELS)


def write_csv(path, rows, fields=None):
    rows = list(rows)
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields or list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def plan_usage(run, result):
    ns = {'p': 'http://schemas.microsoft.com/sqlserver/2004/07/showplan'}
    usage = {}
    for phase in result['phases']:
        scenario = phase['scenario']
        for query in result['queries'][scenario]:
            data = (run / scenario / 'plans' / (query + '.xml')).read_bytes()
            raw = data.decode('utf-16' if data.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig')
            # sqlcmd output can include headings and more than one ShowPlanXML document.
            # -w 65535 also inserts hard wraps inside XML tokens and entities.
            raw = ''.join(line.rstrip('\r\n') if len(line.rstrip('\r\n')) == 65535 else line
                          for line in raw.splitlines(keepends=True))
            documents = re.findall(r'<(?:[\w.-]+:)?ShowPlanXML\b.*?</(?:[\w.-]+:)?ShowPlanXML>', raw, re.S)
            if not documents:
                raise ValueError(f'No estimated plan: {scenario}/{query}')
            for document in documents:
                for obj in ET.fromstring(document).findall('.//p:Object', ns):
                    index = obj.get('Index', '').strip('[]')
                    if index.startswith('FINDINGS_'):
                        usage.setdefault((scenario, obj.get('Table', ''), index), set()).add(query)
    return [dict(scenario=s, table=t, index=i, distinct_queries=len(q), queries=';'.join(sorted(q)))
            for (s, t, i), q in sorted(usage.items())]


def plot(result, output):
    os.environ.setdefault('MPLCONFIGDIR', str(output / '.mplconfig'))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    plt.rcParams.update({'font.size': 10, 'svg.fonttype': 'none',
                         'axes.spines.top': False, 'axes.spines.right': False})
    phases = result['phases']
    names = [p['scenario'] for p in phases]
    labels = [LABELS[n] for n in names]

    def save(fig, name):
        fig.tight_layout()
        for extension in ('png', 'svg'):
            fig.savefig(output / f'{name}.{extension}', dpi=220, bbox_inches='tight')
        plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(15, 6))
    times = [p['sum_average_seconds'] for p in phases]
    errors = [p['sd_pass_total_seconds'] for p in phases]
    axes[0].barh(labels, times, xerr=errors, color='#1768ac', error_kw={'capsize': 3})
    extent = max(t + e for t, e in zip(times, errors)) or 1
    for i, (t, e) in enumerate(zip(times, errors)):
        axes[0].text(t + e + extent * .02, i, f'{t:.2f}', va='center')
    axes[0].set_xlim(0, extent * 1.3)
    axes[0].set_xlabel('Mean sum of query durations (s)')
    axes[0].set_title('Five measured passes; error bars: ±1 SD')
    sizes = [p['index_mib'] for p in phases]
    axes[1].barh(labels, sizes, color='#009a88')
    for i, size in enumerate(sizes):
        axes[1].text(size, i, f' {size:.2f}', va='center')
    axes[1].set_xlim(0, max(1, max(sizes)) * 1.3)
    axes[1].set_xlabel('Added index reserved storage (MiB)')
    axes[1].set_title('Index storage')
    for ax in axes:
        ax.invert_yaxis()
    save(fig, '01_performance_storage')

    fig, ax = plt.subplots(figsize=(11, 5))
    for p in phases:
        ax.plot(range(1, 6), p['pass_totals_seconds'], marker='o', label=LABELS[p['scenario']])
    ax.set(xlabel='Measured pass', ylabel='Sum of query durations (s)', xticks=range(1, 6))
    ax.grid(alpha=.2)
    ax.legend(fontsize=8)
    save(fig, '02_run_stability')

    base = result['queries']['baseline']
    if 'query_015' in base:
        fig, ax = plt.subplots(figsize=(10, 5))
        means = [result['queries'][n]['query_015']['average_ms'] / 1000 for n in names]
        errors = [result['queries'][n]['query_015']['sd_ms'] / 1000 for n in names]
        ax.barh(labels, means, xerr=errors, color='#1768ac', error_kw={'capsize': 3})
        ax.invert_yaxis()
        ax.set(xlabel='Mean query duration (s)', title='query_015: mean ±1 SD, five measurements')
        save(fig, '03_query015')

    candidates = [n for n in names if n != 'baseline']
    top = sorted(base, key=lambda q: base[q]['average_ms'], reverse=True)[:20]
    values = np.array([[result['queries'][n][q]['speedup'] or float('nan') for n in candidates] for q in top])
    fig, ax = plt.subplots(figsize=(11, 9))
    img = ax.imshow(np.log2(values), aspect='auto', cmap='RdYlGn', vmin=-3, vmax=3)
    ax.set_xticks(range(len(candidates)), [LABELS[n] for n in candidates], rotation=25, ha='right')
    ax.set_yticks(range(len(top)), top)
    for i in range(len(top)):
        for j in range(len(candidates)):
            ax.text(j, i, f'{values[i,j]:.2f}x', ha='center', va='center', fontsize=8)
    ax.set_title('Slowest baseline queries: speedup (baseline / scenario)')
    fig.colorbar(img, ax=ax, label='log2(speedup); colour scale clipped to ±3')
    save(fig, '04_query_speedups')


def analyze(run):
    run = Path(run).resolve()
    output = run / 'analysis'
    output.mkdir(exist_ok=True)
    (output / 'STATUS').write_text('VALIDATING\n', encoding='utf-8')
    try:
        result = analyze_run(run)
        if not result['complete'] or result['manifest']['scenarios'].split() != SCENARIOS:
            raise ValueError('Six complete scenarios in the prescribed order are required')
    except Exception:
        (output / 'STATUS').write_text('VALIDATION_FAILED\n', encoding='utf-8')
        raise
    (output / 'STATUS').write_text('GENERATING_OUTPUTS\n', encoding='utf-8')
    rows = []
    for p in result['phases']:
        rows.append(dict(scenario=p['scenario'], label=LABELS[p['scenario']],
                         mean_workload_seconds=p['sum_average_seconds'], sd_workload_seconds=p['sd_pass_total_seconds'],
                         reduction_percent=-p['change_percent'], speedup=p['workload_speedup'],
                         added_index_mib=p['index_mib'], index_setup_seconds=p['index_setup_seconds'],
                         faster_over_5pct=p['faster_over_5pct'], slower_over_5pct=p['slower_over_5pct']))
    write_csv(output / 'scenario_statistics.csv', rows)
    write_csv(output / 'query_statistics.csv', result['comparisons'])
    write_csv(output / 'pass_totals.csv', [dict(scenario=p['scenario'], measured_pass=i, total_seconds=t)
              for p in result['phases'] for i, t in enumerate(p['pass_totals_seconds'], 1)])
    write_csv(output / 'incremental_comparisons.csv', result['incremental_comparisons'],
              ['candidate', 'reference', 'change_percent', 'speedup'])
    usage = plan_usage(run, result)
    write_csv(output / 'estimated_index_usage.csv', usage,
              ['scenario', 'table', 'index', 'distinct_queries', 'queries'])
    checks = [dict(scenario=n, query_id=q, status=s['output_check'], null_warning_count=s['null_warning_count'])
              for n, queries in result['queries'].items() for q, s in queries.items()]
    write_csv(output / 'output_validation.csv', checks)
    result['estimated_index_usage'] = usage
    (output / 'statistics.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    plot(result, output)
    unresolved = [c for c in checks if c['status'] == 'review_values_or_format']
    notes = ['# Experiment results', '',
             'Status: ' + ('OUTPUT REVIEW REQUIRED' if unresolved else 'MEASUREMENTS COMPLETE; no unresolved rendered-row differences'),
             f"Scale: {result['manifest']['scale']}. Queries per scenario: {len(result['queries']['baseline'])}.", '',
             '| Configuration | Mean (s) | SD (s) | Reduction (%) | Indexes (MiB) |',
             '|---|---:|---:|---:|---:|']
    for row in rows:
        notes.append(f"| {row['label']} | {row['mean_workload_seconds']:.2f} | {row['sd_workload_seconds']:.2f} | {row['reduction_percent']:.2f} | {row['added_index_mib']:.2f} |")
    notes += ['', '## Interpretation and limitations',
              '- Time is the mean sum of query durations per pass, including the sqlcmd process. It is not the total script duration.',
              '- Five measured passes and one excluded warm-up pass; error bars show one sample standard deviation in each direction, not confidence intervals.',
              '- Fixed execution order; caches are not cleared. Inspect stability and regressions before interpreting small differences.',
              '- Uniform FULLSCAN before measurement. Index creation is timed separately; write and maintenance costs are not evaluated.',
              '- Warm-up output comparison preserves duplicates and distinguishes ordering changes and the NULL warning. It does not prove formal SQL equivalence or validate required ordering.',
              '- Plans are estimated: index selection does not establish actual execution cost.',
              '- Identifiers such as query_015 are queryset filenames, not necessarily official TPC-DS query numbers.',
              '- Do not extrapolate to unmeasured scales. INCLUDE variants only change store_sales.']
    if result['resumed']:
        notes.append('- RESUMED: retained phases precede the interruption; repeat a clean run before drawing definitive conclusions.')
    if unresolved:
        notes.append(f'- There are {len(unresolved)} unresolved outputs. Inspect output_validation.csv before accepting performance improvements.')
    (output / 'RESULTS.md').write_text('\n'.join(notes) + '\n', encoding='utf-8')
    (output / 'STATUS').write_text('REVIEW_REQUIRED\n' if unresolved else 'COMPLETE\n', encoding='utf-8')
    write_csv(output / 'SHA256.csv', [dict(path=p.relative_to(run).as_posix(), sha256=hashlib.sha256(p.read_bytes()).hexdigest())
              for p in sorted(run.rglob('*')) if p.is_file() and p.name != 'SHA256.csv' and '.mplconfig' not in p.parts])
    print(f'Results: {output}')
    return 2 if unresolved else 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('run', type=Path)
    raise SystemExit(analyze(parser.parse_args().run))
