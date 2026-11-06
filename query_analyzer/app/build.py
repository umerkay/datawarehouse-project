"""Generate all pages from one configuration, including explicit pending states."""
from pathlib import Path
import csv
import io

from ..core.common import ROOT, SCALES, atomic_write, write_json
from ..web.common import navigation, pending_report, report_name, template
from ..core.sources import load_sources
from ..web.comparison import render as render_comparison
from ..web.dashboard import evidence, render as render_dashboard
from ..web.experiments import render as render_experiments
from ..core.strategies import strategies
from ..web.strategies import page as strategy_page
from ..core.index_comparison import compare_indexes
from ..web.index_comparison import render as render_indexes
from ..core.overall import overall_analysis
from ..web.overall import render as render_overall


def payload_for(scale, source):
    queries = []
    for query_id, plan in source['plans'].items():
        query = {key: value for key, value in plan.items() if key not in ('shape', 'trees', 'sql')}
        query['average_ms'] = source['timings'].get(query_id)
        query['index_inventory'] = source.get('index_inventory')
        queries.append(query)
    queries.sort(key=lambda q: (q['average_ms'] is not None, q['average_ms'] or 0, q['estimated_cost']), reverse=True)
    return dict(scale=scale, queries=queries, strategies=strategies(queries), errors=source['errors'], timing_source=source['timing_source'])


def copy_assets(output):
    target = Path(output) / 'assets'
    if target.resolve() == (ROOT / 'web' / 'assets').resolve():
        return
    target.mkdir(parents=True, exist_ok=True)
    for asset in list((ROOT / 'web' / 'assets').iterdir()):
        if asset.is_file():
            atomic_write(target / asset.name, asset.read_text(encoding='utf-8'))


def build_site(config, output):
    sources = load_sources(Path(config))
    output = Path(output)
    copy_assets(output)
    coverage = []
    payloads = []
    errors = []
    for scale in SCALES:
        source = sources[scale]
        payload = payload_for(scale, source)
        payloads.append(payload)
        suffix = '' if scale == '1GB' else '_' + scale
        write_json(output / ('analysis' + suffix + '.json'), payload)
        if payload['queries']:
            page = render_dashboard(payload, ''.join(evidence(q) for q in payload['queries']))
        else:
            page = pending_report(scale, source['errors'])
        atomic_write(output / report_name(scale), page)
        count = len(source['plans'])
        status = f'{count} execution plans' if count else 'Awaiting execution plans'
        coverage.append(f'<article class="card"><h2>{scale}</h2><p><strong>{status}</strong></p><p>{len(source["timings"])} historical timing rows</p><a href="{report_name(scale)}">Open {scale} analysis</a></article>')
        errors.extend(scale + ': ' + error for error in source['errors'])
    atomic_write(output / 'comparison.html', render_comparison(sources))
    index_groups = compare_indexes(sources)
    overall = overall_analysis(payloads, index_groups)
    overall_page, figures = render_overall(overall)
    atomic_write(output / 'overall.html', overall_page)
    write_json(output / 'overall_analysis.json', overall)
    for name, svg in figures.items():
        atomic_write(output / 'figures' / (name + '.svg'), svg)
    atomic_write(output / 'indexes.html', render_indexes(index_groups))
    write_json(output / 'index_recommendations.json', index_groups)
    csv_stream = io.StringIO(newline='')
    writer = csv.writer(csv_stream)
    writer.writerow(['schema', 'table', 'equality_keys', 'range_keys', *SCALES])
    for group in index_groups:
        writer.writerow([group['schema'], group['table'], ', '.join(group['equality']), ', '.join(group['ranges']), *[len(group['datasets'][scale]['queries']) if group['datasets'][scale]['available'] else '' for scale in SCALES]])
    atomic_write(output / 'index_recommendations.csv', csv_stream.getvalue())
    atomic_write(output / 'strategies.html', strategy_page(payloads))
    atomic_write(output / 'experiments.html', render_experiments(sources))
    atomic_write(output / 'index.html', template('index.html', dict(NAV=navigation('index.html'), COVERAGE=''.join(coverage))))
    atomic_write(output / '.nojekyll', '')
    return sources, errors
