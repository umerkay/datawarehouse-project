"""Present strategies with traceable evidence and explicit experiments."""
from .common import navigation, report_name
from .plan_views import esc
import json
from collections import Counter


def cards(findings, report=None):
    if not findings:
        return '<p>No strategy meets the evidence rules for the available plans. Missing plans are not interpreted as an absence of optimization opportunities.</p>'
    content = '<p>Start with a strategy, inspect the supporting queries, then choose one experiment. SQL Server suggestions and analyzer hypotheses are labelled separately. Groups are ordered by strategy type and query coverage, not predicted speedup.</p>'
    families = {'index': 'Rowstore index candidates', 'filter': 'Selective filtering', 'columnstore': 'Columnstore experiments', 'aggregate': 'Reusable pre-aggregations', 'ordering': 'Recurring sort requirements'}
    counts = Counter(finding['kind'] for finding in findings)
    current = None
    for finding in findings:
        if finding['kind'] != current:
            if current is not None:
                content += '</details>'
            current = finding['kind']
            content += '<details class="strategy-family"><summary>' + families[current] + ' (' + str(counts[current]) + ' candidates)</summary>'
        query_links = []
        for query in finding['queries']:
            target = (report or '') + '#detail-' + query
            behavior = '' if report else ' data-query="' + esc(query) + '"'
            query_links.append('<a href="' + esc(target) + '"' + behavior + '>' + esc(query) + '</a>')
        title = finding['title'] + ' — ' + finding['table']
        if finding['signature']:
            pattern = json.loads(finding['signature'])
            if finding['kind'] == 'index':
                pattern = ['Equality: ' + (', '.join(pattern[0]) or 'none'), 'Range: ' + (', '.join(pattern[1]) or 'none')]
            title += ' · ' + '; '.join(pattern)
        count = len(finding['queries'])
        content += '<details class="strategy-card"><summary>' + esc(title) + ' <span class="pill">' + str(count) + (' query' if count == 1 else ' queries') + '</span></summary>'
        content += '<p><b>Basis:</b> ' + esc(finding['origin']) + '</p><p><b>Queries:</b> ' + ', '.join(query_links) + '</p>'
        content += '<p><b>Experiment:</b> ' + esc(finding['experiment']) + '</p><p><b>Before deciding:</b> ' + esc(finding['limitation']) + '</p>'
        content += '<details><summary>Plan evidence (' + str(len(finding['evidence'])) + ' records)</summary><ul>'
        for evidence in finding['evidence']:
            label = evidence['query_id'] + ', statement ' + str(evidence['statement_id'])
            if evidence['node'] is not None:
                label += ', node ' + str(evidence['node'])
            content += '<li><b>' + esc(label) + ':</b> ' + esc(evidence['detail']) + '</li>'
        content += '</ul></details></details>'
    return content + '</details>'


def page(payloads):
    sections = ''
    for payload in payloads:
        sections += '<section class="panel"><h2>' + esc(payload['scale']) + '</h2>' + cards(payload['strategies'], report_name(payload['scale'])) + '</section>'
    return '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Optimization strategies</title><link rel="stylesheet" href="assets/dashboard.css"></head><body><header><h1>Optimization strategies</h1><p>From estimated-plan evidence to experiments worth testing.</p></header><main>' + navigation('strategies.html') + '<p>Findings are separated by dataset. Query counts describe coverage, not guaranteed benefit. No queries or database changes are executed.</p>' + sections + '</main></body></html>'
