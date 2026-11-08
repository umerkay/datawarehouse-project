"""Cross-size index evidence and INCLUDE request comparison."""
from .common import navigation, report_name
from .plan_views import esc
from ..core.common import SCALES


def table(headers, rows):
    return '<div class="scroll"><table><thead><tr>' + ''.join('<th>' + esc(h) + '</th>' for h in headers) + '</tr></thead><tbody>' + ''.join('<tr>' + ''.join('<td>' + cell + '</td>' for cell in row) + '</tr>' for row in rows) + '</tbody></table></div>'


def render(groups):
    rows, date_rows, details = [], [], []
    for number, group in enumerate(groups, 1):
        title = '.'.join(filter(None, (group['schema'], group['table'])))
        keys = 'Equality: ' + (', '.join(group['equality']) or 'none') + '; range: ' + (', '.join(group['ranges']) or 'none')
        rows.append(['<a href="#index-' + str(number) + '">' + esc(title) + '</a>', esc(keys)] + [str(len(group['datasets'][scale]['queries'])) if group['datasets'][scale]['available'] else 'Pending' for scale in SCALES])
        if len(group['equality']) == 1 and group['equality'][0].endswith('_sold_date_sk]') and not group['ranges']:
            date_rows.append(rows[-1])
        body = ''
        for scale in SCALES:
            data = group['datasets'][scale]
            if not data['available']:
                body += '<h3>' + scale + '</h3><p>Plans pending.</p>'
                continue
            if not data['queries']:
                body += '<h3>' + scale + '</h3><p>No matching request in the captured plans.</p>'
                continue
            links = lambda queries: ', '.join('<a href="' + report_name(scale) + '#detail-' + esc(q) + '">' + esc(q) + '</a>' for q in queries)
            body += '<h3>' + scale + '</h3><p>' + str(len(data['queries'])) + ' distinct queries; ' + str(len(data['variants'])) + ' distinct INCLUDE sets; union: ' + str(len(data['union'])) + ' columns.</p>'
            body += '<p><b>Common to every recorded INCLUDE set:</b> ' + esc(', '.join(data['common']) or 'none') + '.</p>'
            body += '<p><b>Union of requested INCLUDE columns:</b> ' + esc(', '.join(data['union']) or 'none') + '.</p>'
            body += '<details><summary>Column frequency</summary>' + table(['Column', 'Queries requesting it'], [[esc(f['column']), str(len(f['queries'])) + '<br>' + links(f['queries'])] for f in data['frequencies']]) + '</details>'
            body += table(['Recorded INCLUDE set', 'Queries with this exact set', 'Queries whose full INCLUDE requests fit this set'], [[esc(', '.join(v['columns']) or '(no INCLUDE)'), str(len(v['exact_queries'])) + '<br>' + links(v['exact_queries']), str(len(v['covered_queries'])) + ' / ' + str(len(data['queries'])) + '<br>' + links(v['covered_queries'])] for v in data['variants']])
        details.append('<details class="strategy-card" id="index-' + str(number) + '"><summary>' + esc(title + ' — ' + keys) + '</summary>' + body + '</details>')
    headers = ['Table', 'Suggested key pattern', *SCALES]
    summary = '<h3>Recurring sales-date key recommendations</h3>' + table(headers, date_rows) + '<details><summary>All suggested key patterns</summary>' + table(headers, rows) + '</details>'
    return '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Index recommendation comparison</title><link rel="stylesheet" href="assets/dashboard.css"></head><body><header><h1>Index recommendation comparison</h1><p>SQL Server missing-index evidence across dataset sizes.</p></header><main>' + navigation('indexes.html') + '<section class="panel"><h2>Recommendations across sizes</h2><p>Counts are distinct queries for the same table and key pattern, independent of INCLUDE variants. Equality-key order is not inferred; range-key order is preserved. Query identifiers do not guarantee identical SQL across sizes.</p><p><a href="index_recommendations.csv" download>Download counts as CSV</a> · <a href="index_recommendations.json" download>Download full evidence as JSON</a></p>' + summary + '</section><section class="panel"><h2>Compare INCLUDE requests</h2><p>Click a table above, then open its comparison. INCLUDE columns are requested for coverage rather than as search keys. The comparison below checks requested column sets only: a query counts as fitting a set only when all its recorded INCLUDE requests for this key pattern fit. This is not proof of index coverage, optimizer use or speedup. Existing indexes, implicit clustered keys, key order, data types and storage costs require separate review.</p><p>The union would satisfy the recorded column requests, but is not an automatic recommendation to create a wide index. An empty INCLUDE set means no extra columns were requested in that suggestion, not that the query needs no other columns.</p>' + ''.join(details) + '</section></main></body></html>'
