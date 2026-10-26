"""Screenshot-ready figures and auditable tables for the overall plan analysis."""
import math
from .common import navigation, report_name
from .plan_views import esc
from ..core.common import SCALES


COLORS = ('#1768ac', '#009a88', '#d28413', '#8649ae')


def chart(title, rows):
    """All bars share a zero origin and one scale; absent evidence is explicit."""
    peak = max((value for _, values in rows for value in values if value is not None), default=0)
    bound = max(5, math.ceil(peak / 5) * 5)
    height = 120 + len(rows) * 132
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1160 {height}" role="img" aria-label="{esc(title)}"><title>{esc(title)}</title><rect width="1160" height="{height}" fill="white"/><g font-family="Arial,sans-serif" fill="#172b40"><text x="24" y="30" font-size="20" font-weight="bold">{esc(title)}</text>'
    for i, scale in enumerate(SCALES):
        x = 250 + i * 160
        svg += f'<rect x="{x}" y="48" width="14" height="14" fill="{COLORS[i]}"/><text x="{x + 22}" y="60" font-size="14">{scale}</text>'
    for tick in range(6):
        value = bound * tick / 5
        x = 320 + 760 * tick / 5
        svg += f'<line x1="{x}" x2="{x}" y1="82" y2="{height - 32}" stroke="#e0e6ee"/><text x="{x}" y="{height - 10}" text-anchor="middle" font-size="13">{value:g}</text>'
    for row_number, (label, values) in enumerate(rows):
        y = 102 + row_number * 132
        svg += f'<text x="24" y="{y + 13}" font-size="15">{esc(label)}</text>'
        for i, value in enumerate(values):
            top = y + i * 24
            if value is None:
                svg += f'<text x="328" y="{top + 13}" font-size="13">Pending</text>'
            else:
                width = 760 * value / bound
                svg += f'<rect x="320" y="{top}" width="{width:.2f}" height="17" rx="2" fill="{COLORS[i]}"/><text x="{328 + width:.2f}" y="{top + 13}" font-size="13">{value}</text>'
    return svg + '</g></svg>'


def table(headers, rows):
    return '<div class="overall-table"><table><thead><tr>' + ''.join('<th>' + esc(h) + '</th>' for h in headers) + '</tr></thead><tbody>' + ''.join('<tr>' + ''.join('<td>' + cell + '</td>' for cell in row) + '</tr>' for row in rows) + '</tbody></table></div>'


def counts(item):
    return [len(item['datasets'][scale]['queries']) if item['datasets'][scale]['available'] else None for scale in SCALES]


def count_cells(item):
    return [str(value) if value is not None else 'Pending' for value in counts(item)]


def evidence(items):
    body = ''
    for item in items:
        label = item.get('label') or '.'.join(filter(None, (item.get('schema'), item.get('table'))))
        body += '<details><summary>' + esc(label) + '</summary>'
        for scale in SCALES:
            data = item['datasets'][scale]
            links = ', '.join('<a href="' + report_name(scale) + '#detail-' + esc(query) + '">' + esc(query) + '</a>' for query in data['queries'])
            body += '<p><b>' + scale + ':</b> ' + (links or ('No matching queries' if data['available'] else 'Plans pending')) + '</p>'
            if data.get('evidence'):
                body += '<details><summary>Statement and node evidence</summary><ul>' + ''.join('<li>' + esc(f"{e['query_id']}, statement {e['statement_id']}, node {e['node']}: {e['detail']}") + '</li>' for e in data['evidence']) + '</ul></details>'
        body += '</details>'
    return '<details class="evidence"><summary>Supporting queries and plan evidence</summary>' + body + '</details>'


def render(data):
    figures = {}

    def figure(name, title, rows, caption):
        figures[name] = chart(title, rows)
        return '<figure class="report-figure">' + figures[name] + '<figcaption>' + esc(caption) + '</figcaption><a class="download" href="figures/' + name + '.svg" download>Download figure (SVG)</a></figure>'

    date_indexes = data['date_indexes']
    index_rows = [(g['table'].strip('[]') + ' / ' + g['equality'][0].strip('[]'), counts(g)) for g in date_indexes]
    first = figure('date-indexes', 'SQL Server suggestions on sales-date keys', index_rows,
                   'Figure 1. Distinct queries requesting each date-key index. INCLUDE variants are counted together; a query is counted once per key pattern and dataset.')
    first += table(['Table', 'Equality key', *SCALES], [[esc(g['table']), esc(', '.join(g['equality'])), *count_cells(g)] for g in date_indexes])
    first += evidence(date_indexes)

    facts = {'[dbo].[store_sales]', '[dbo].[catalog_sales]', '[dbo].[web_sales]'}
    scans = [p for p in data['patterns'] if p['kind'] == 'scan_aggregate' and p['label'] in facts]
    second = figure('scans-aggregates', 'Fact-table scans upstream of aggregation', [(p['label'].replace('[dbo].', '').strip('[]'), counts(p)) for p in scans],
                    'Figure 2. Distinct queries with at least one qualifying scan feeding an aggregate, possibly through joins. Counts describe plan structure, not measured bottlenecks or columnstore benefits.')
    second += '<p class="definition">Definition: a non-columnstore scan with at least 10,000 estimated output rows lies on an input branch of an aggregate. Each query is counted once per table and dataset.</p>'
    second += table(['Fact table', *SCALES], [[esc(p['label']), *count_cells(p)] for p in scans]) + evidence(scans)

    third = '<p>For the same date-key pattern, compare how many different INCLUDE sets SQL Server requests and how many columns their union contains.</p>'
    third += table(['Table', *SCALES], [[esc(g['table']), *[str(len(g['datasets'][s]['variants'])) + ' sets / ' + str(len(g['datasets'][s]['union'])) + ' columns' if g['datasets'][s]['available'] else 'Pending' for s in SCALES]] for g in date_indexes])
    for g in date_indexes:
        third += '<details' + (' open' if g['table'] == '[store_sales]' else '') + '><summary>' + esc(g['table'] + ' — ' + ', '.join(g['equality'])) + '</summary>'
        for scale in SCALES:
            d = g['datasets'][scale]
            if not d['queries']:
                continue
            third += '<details' + (' open' if scale == '10GB' and g['table'] == '[store_sales]' else '') + '><summary>' + scale + ' — ' + str(len(d['queries'])) + ' queries</summary>'
            third += '<p>' + str(len(d['variants'])) + ' distinct INCLUDE sets; ' + str(len(d['union'])) + ' columns in their union. Common to every set: ' + esc(', '.join(d['common']) or 'none') + '.</p>'
            # Frequency is queried independently for each column; columns overlap.
            height = 72 + len(d['frequencies']) * 29
            max_count = max(1, len(d['queries']))
            name = 'includes-' + g['table'].strip('[]') + '-' + scale
            svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1160 {height}" role="img" aria-label="INCLUDE column frequency"><title>INCLUDE column frequency</title><rect width="1160" height="{height}" fill="white"/><g font-family="Arial,sans-serif" fill="#172b40"><text x="24" y="28" font-size="20" font-weight="bold">{esc(g["table"])} / {scale}: queries requesting each INCLUDE column</text>'
            for i, f in enumerate(d['frequencies']):
                y = 50 + i * 29
                width = 680 * len(f['queries']) / max_count
                svg += f'<text x="24" y="{y+14}" font-size="14">{esc(f["column"])}</text><rect x="300" y="{y}" width="{width}" height="19" fill="#1768ac"/><text x="{310+width}" y="{y+14}" font-size="14">{len(f["queries"])} / {max_count}</text>'
            figures[name] = svg + '</g></svg>'
            third += '<figure class="report-figure">' + figures[name] + '<figcaption>Distinct queries requesting each column; the same query may request several columns.</figcaption><a href="figures/' + name + '.svg" download>Download figure (SVG)</a></figure>'
            variants = sorted(d['variants'], key=lambda v: (-len(v['covered_queries']), len(v['columns']), v['columns']))
            third += table(['Recorded INCLUDE set', 'Queries with this exact set', 'Queries whose full requests fit'], [[esc(', '.join(v['columns']) or '(no INCLUDE)'), str(len(v['exact_queries'])), str(len(v['covered_queries'])) + ' / ' + str(len(d['queries']))] for v in variants])
            third += '<p class="definition">A set fits a query only if all its recorded INCLUDE requests for this key pattern are contained in that set. This checks requested columns, not physical coverage or performance. The union is not an automatic index recommendation.</p></details>'
        third += '</details>'
    third += '<p><a href="indexes.html">Inspect all key patterns and query-level INCLUDE evidence</a></p>'

    fourth = ''
    for kind, title, note in [('grouping', 'Recurring grouping columns', 'Same grouping columns with aggregate expressions recorded; measures, joins and filters may differ. This does not establish reusable results.'), ('ordering', 'Recurring sort requirements', 'Same resolved columns, sequence and direction. An index does not necessarily remove a sort through joins or parallel exchanges.')]:
        items = [p for p in data['patterns'] if p['kind'] == kind and max(len(d['queries']) for d in p['datasets'].values()) >= 2][:6]
        fourth += '<h3>' + title + '</h3><p>' + note + '</p>' + table(['Columns', *SCALES], [[esc(p['label']), *count_cells(p)] for p in items]) + evidence(items)
    filters = [p for p in data['patterns'] if p['kind'] == 'filtered_scan'][:5]
    fourth += '<details><summary>Other observations: scan filtering and engine warnings</summary><h3>Scans with estimated output at most 10% of rows read</h3><p>Requires at least 10,000 estimated rows read and a residual predicate. Bitmap predicates are included. These estimates alone do not prove a missing index.</p>' + table(['Table', *SCALES], [[esc(p['label']), *count_cells(p)] for p in filters]) + evidence(filters)
    warnings = [p for p in data['patterns'] if p['kind'] == 'no_join_predicate']
    fourth += '<h3>NoJoinPredicate warnings</h3>' + table(['Query', *SCALES], [[esc(p['label']), *['Present' if d else ('Pending' if d is None else 'Not recorded') for d in counts(p)]] for p in warnings]) + evidence(warnings) + '</details>'

    panels = [('index-patterns', '1. Shared index recommendations', first), ('scan-patterns', '2. Scans and aggregation', second), ('include-patterns', '3. INCLUDE requirements', third), ('recurring-patterns', '4. Other recurring patterns', fourth)]
    coverage = ''.join('<div><strong>' + str(data['coverage'][s]) + '</strong><span>' + s + ' plans</span></div>' for s in SCALES)
    errors = '<p class="notice">Some inputs could not be analyzed; counts cover only available plans. Review dataset diagnostics.</p>' if any(data['errors'].values()) else ''
    html = '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Overall plan analysis</title><link rel="stylesheet" href="assets/dashboard.css"><link rel="stylesheet" href="assets/overall.css"></head><body><header><div class="eyebrow">TPC-DS / estimated execution plans</div><h1>Overall analysis</h1><p>Shared evidence across dataset sizes, ready to use in your report.</p></header><main>' + navigation('overall.html') + '<div class="overall-coverage">' + coverage + '</div>' + errors + '<nav class="overall-sections" aria-label="Analysis sections">' + ''.join('<a href="#' + key + '">' + title + '</a>' for key, title, _ in panels) + '</nav><p>Counts refer to distinct queries within each dataset, not operators or measured savings. Missing plans remain pending. Compare observed patterns across sizes; matching filenames do not establish identical SQL or server conditions.</p>' + ''.join('<section class="panel" id="' + key + '"><h2>' + title + '</h2>' + body + '</section>' for key, title, body in panels) + '<p><a href="overall_analysis.json" download>Download all supporting evidence (JSON)</a> · <a href="index_recommendations.csv" download>Download index counts (CSV)</a></p></main></body></html>'
    return html, figures
