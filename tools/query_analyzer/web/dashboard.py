"""Render dashboard content on generation, with optional browser filters."""
import html
import json
from collections import Counter, defaultdict
from .common import navigation, report_name, template
from .plan_views import plan_tree, index_review
from .strategies import cards as strategy_cards


def esc(value):
    return html.escape(str(value))


LABELS = {'missing': 'Index candidate', 'scan': 'Large scan',
          'warning': 'Engine warning', 'cardinality': 'Row estimate mismatch >=10x',
          'work': 'Sort / hash / spool'}


def evidence(query):
    """Explain repeated plan patterns once; retain raw evidence behind disclosures."""
    def table(headers, rows):
        return '<div class="scroll"><table><thead><tr>' + ''.join('<th>' + esc(h) + '</th>' for h in headers) + '</tr></thead><tbody>' + ''.join('<tr>' + ''.join('<td>' + esc(v) + '</td>' for v in row) + '</tr>' for row in rows) + '</tbody></table></div>'

    avg = f'{query["average_ms"]:,.1f} ms' if query['average_ms'] is not None else 'Not measured'
    body = f'<p><b>Profiled average:</b> {avg}. <b>Estimated optimizer cost:</b> {query["estimated_cost"]:,.2f} (not milliseconds).</p>'
    for stmt in query['statements']:
        ops = stmt['operators']
        counts = Counter(o['physical'] for o in ops)
        scans = defaultdict(list)
        for op in ops:
            if 'Scan' in (op['physical'] or ''):
                for obj in op['objects']:
                    name = '.'.join(filter(None, (obj.get('Schema'), obj.get('Table'))))
                    scans[name].append(op)
        scan_groups = sorted(scans.items(), key=lambda pair: sum(o['local_cost'] for o in pair[1]), reverse=True)
        warnings = [f for f in stmt['findings'] if 'Warnings' in f]
        mismatches = [o for o in ops if o['cardinality_ratio'] == 'infinite' or isinstance(o['cardinality_ratio'], (int, float)) and o['cardinality_ratio'] >= 10]
        missing = list({json.dumps([m['database'], m['schema'], m['table'], m['columns']], sort_keys=True): m for m in stmt['missing_indexes']}.values())
        body += f'<h3>Statement {esc(stmt["statement_id"])} - {"Actual" if stmt["actual"] else "Estimated"} plan</h3>'
        if not stmt['actual']:
            body += '<p class="muted">This plan predicts work. It does not measure time per operation, actual rows, or spills during execution.</p>'
        elif any(o.get('parallel_counters') for o in ops):
            body += '<p class="muted">Actual rows and thread execution totals are preserved. Cardinality mismatch is not inferred for operators with multiple active workers.</p>'
        body += '<h4>What stands out</h4><ul>'
        if scan_groups:
            names = ', '.join(f'{name}: {len(items)} scan operators' for name, items in scan_groups[:3])
            body += '<li><b>Table access:</b> ' + esc(names) + '. Repeated operators are separate access points in the plan, not a measured execution count.</li>'
        hashes = counts['Hash Match']
        if hashes:
            body += f'<li><b>Combining rows:</b> {hashes} hash operations join, group, or compare row sets. Their presence alone does not indicate a problem.</li>'
        sorts = counts['Sort']
        spools = sum(count for operation, count in counts.items() if 'Spool' in (operation or ''))
        if sorts or spools:
            body += f'<li><b>Intermediate work:</b> {sorts} sorting and {spools} spool operators. Sorting orders rows; spools hold intermediate results for reuse.</li>'
        body += f'<li><b>Optimizer suggestions:</b> {len(missing)} distinct index candidate(s). These are hypotheses to compare against existing indexes.</li>'
        if warnings:
            body += f'<li><b>Engine warnings:</b> {len(warnings)} operator(s) have warnings. Expand the node evidence for the exact messages.</li>'
        if mismatches:
            body += f'<li><b>Row estimates:</b> {len(mismatches)} operator(s) differ from actual rows by at least 10x. Review statistics and predicates.</li>'
        body += '</ul>'
        body += plan_tree(stmt)
        if missing:
            body += index_review(missing, query.get('index_inventory'))
        if scan_groups:
            body += '<h4>Where the plan accesses tables</h4><p class="muted">Up to five tables, ranked by summed local estimated scan cost. Row counts are estimated output per operator, not disk reads.</p>'
            rows = [(name, len(items), f'{max(o["estimated_rows"] for o in items):,.0f}', f'{sum(o["local_cost"] for o in items):,.2f}') for name, items in scan_groups[:5]]
            body += table(['Table', 'Scan operators', 'Largest estimated row output', 'Estimated scan cost'], rows)
        body += '<h4>What to investigate first</h4><ol>'
        if mismatches:
            body += '<li>Check statistics and filter estimates where actual row counts differ substantially.</li>'
        if scan_groups:
            body += '<li>Start with ' + esc(scan_groups[0][0]) + ': check whether filters and joins can use a more selective or covering index. Broad analytical scans may still be appropriate.</li>'
        if missing:
            body += '<li>Compare the index candidates below with existing indexes; test a consolidated candidate before creating several overlapping indexes.</li>'
        if not stmt['actual']:
            body += '<li>Capture an actual plan for this query to confirm row counts and memory warnings before attributing the runtime to a specific operation.</li>'
        body += '<li>Measure the proposed change with the same dataset and warm-up protocol, and verify equivalent results.</li></ol>'
        if missing:
            rows = [('.'.join(filter(None, (m['schema'], m['table']))), ', '.join(m['columns'].get('EQUALITY', [])), ', '.join(m['columns'].get('INEQUALITY', [])), ', '.join(m['columns'].get('INCLUDE', []))) for m in missing]
            body += f'<details><summary>Index candidate definitions ({len(missing)})</summary>' + table(['Table', 'Equality keys', 'Range keys', 'Included columns'], rows) + '</details>'
        body += '<details><summary>Query SQL</summary><pre>' + esc(stmt['sql']) + '</pre></details>'
        body += f'<details><summary>Advanced: all {len(ops)} operators and node messages</summary><p class="muted">A node is one operation in the execution plan. Local costs are optimizer estimates, not measured durations. Repeated messages are grouped in the summary above.</p>'
        if stmt['findings']:
            body += '<details><summary>Raw node messages</summary><ul>' + ''.join('<li>' + esc(f) + '</li>' for f in stmt['findings']) + '</ul></details>'
        rows = [(o['node'], o['physical'], o['logical'], ', '.join('.'.join(filter(None, (obj.get('Schema'), obj.get('Table')))) for obj in o['objects']), f'{o["local_cost"]:,.3f}', f'{o["estimated_rows"]:,.0f}', o['actual_rows'] if o['actual_rows'] is not None else 'Not available', o['actual_executions'] if o['actual_executions'] is not None else 'Not available') for o in ops]
        body += table(['Node', 'Operation', 'Purpose', 'Table', 'Estimated local cost', 'Estimated rows / execution', 'Actual rows total', 'Thread executions (sum)'], rows) + '</details>'
    return '<details class="query-detail" id="detail-' + esc(query['query_id']) + '"><summary>' + esc(query['query_id']) + ' - View technical evidence</summary>' + body + '</details>'


def render(payload, details):
    queries = []
    for query in payload['queries']:
        operators = [o for s in query['statements'] for o in s['operators']]
        missing = [m for s in query['statements'] for m in s['missing_indexes']]
        flags = dict(missing=bool(missing),
                     scan=any('Scan' in (o['physical'] or '') and o['estimated_rows'] >= 10000 for o in operators),
                     warning=any('Warnings' in f for s in query['statements'] for f in s['findings']),
                     cardinality=any(o['cardinality_ratio'] == 'infinite' or
                                     isinstance(o['cardinality_ratio'], (int, float)) and o['cardinality_ratio'] >= 10 for o in operators),
                     work=any(o['physical'] in ('Sort', 'Hash Match', 'Table Spool', 'Index Spool') for o in operators))
        tables = sorted({'.'.join(filter(None, (obj.get('Schema'), obj.get('Table'))))
                         for o in operators for obj in o['objects']})
        queries.append(dict(query, flags=flags, missing=missing, tables=tables))
    measured = [q for q in queries if q['average_ms'] is not None]
    metric = 'average_ms' if measured else 'estimated_cost'
    ranked = sorted(measured or queries, key=lambda q: q[metric], reverse=True)
    leaders = ranked[:10]
    total = sum(q[metric] for q in ranked)
    fmt = lambda n: f'{n:,.1f}'
    link = lambda q: f'<a href="#detail-{esc(q)}" data-query="{esc(q)}">{esc(q)}</a>'
    status = ''
    if not measured:
        status += '<div class="notice">No measured timings. Ranking uses optimizer cost, not execution time. Add the profiler summary.csv with --summary.</div>'
    elif len(measured) < len(queries):
        status += f'<div class="notice">Partial timing coverage: {len(measured)} of {len(queries)} queries. Percentages cover measured queries only.</div>'
    if payload['errors']:
        status += f'<div class="notice">{len(payload["errors"])} input file(s) could not be analyzed.<details><summary>Show errors</summary><pre>{esc(json.dumps(payload["errors"], indent=2))}</pre></details></div>'
    cards = ''.join(f'<div class="card"><span class="muted">{label}</span><strong>{count}</strong></div>' for label, count in [
        ('Queries analyzed', len(queries)), ('With measured timings', len(measured)),
        ('With index candidates', sum(q['flags']['missing'] for q in queries)),
        ('With engine warnings', sum(q['flags']['warning'] for q in queries))])
    basis = ('Top 10 by measured average duration. Source: ' + str(payload.get('timing_source')) +
             '. Matched by filename: verify that timings and plans use the same SQL version.' if measured else
             'Top 10 by estimated optimizer cost. Cost units are not milliseconds.')
    bars = ''
    for q in leaders:
        width = 100 * q[metric] / leaders[0][metric] if leaders[0][metric] else 0
        bars += f'<div class="barrow">{link(q["query_id"])}<div class="track"><div class="bar" style="width:{width:.2f}%"></div></div><span class="value">{fmt(q[metric])}{" ms" if measured else " cost"}</span></div>'
    concentration = (f'These {len(leaders)} queries account for {100 * sum(q[metric] for q in leaders) / total:.1f}% of '
                     f'{"summed average measured time" if measured else "summed estimated cost"}. This is not guaranteed savings.' if total else '')
    patterns = ''
    for key, label in LABELS.items():
        count = sum(q['flags'][key] for q in queries)
        width = count / len(queries) * 100 if queries else 0
        patterns += f'<div class="barrow"><span>{label}</span><div class="track"><div class="bar" style="width:{width:.2f}%"></div></div><span class="value">{count} queries</span></div>'
    grouped = {}
    for q in queries:
        for m in q['missing']:
            key = json.dumps([m['database'], m['schema'], m['table'], m['columns']], sort_keys=True)
            if key not in grouped:
                grouped[key] = dict(m, queries=set())
            grouped[key]['queries'].add(q['query_id'])
    candidates = sorted(grouped.values(), key=lambda m: (-len(m['queries']), str(m['table'])))
    indexes = '<p class="empty">No index candidates in these plans. Existing indexes may still need investigation.</p>'
    if candidates:
        rows = []
        for m in candidates:
            cells = [esc('.'.join(filter(None, (m['database'], m['schema'], m['table']))))]
            cells.extend(esc(', '.join(m['columns'].get(k, []))) for k in ('EQUALITY', 'INEQUALITY', 'INCLUDE'))
            cells.append(f'<b>{len(m["queries"])}</b><br>' + ' · '.join(link(q) for q in sorted(m['queries'])))
            rows.append('<tr>' + ''.join(f'<td>{cell}</td>' for cell in cells) + '</tr>')
        indexes = '<table><thead><tr><th>Table</th><th>Equality keys</th><th>Range keys</th><th>Included columns</th><th>Queries affected</th></tr></thead><tbody>' + ''.join(rows) + '</tbody></table>'
    steps = [
        'Start with ' + ', '.join(q['query_id'] for q in leaders[:3]) + '; inspect their signals and evidence.' if leaders else 'Capture valid plans to start.',
        f'Compare the candidate on {candidates[0]["schema"]}.{candidates[0]["table"]}, suggested for {len(candidates[0]["queries"])} queries, against existing indexes.' if candidates else 'Review dominant operators; no explicit index candidates were found.',
        'Review statistics and predicates where actual and estimated rows differ.' if any(q['flags']['cardinality'] for q in queries) else 'Capture actual plans for priority queries to confirm row counts and memory behavior.',
        'Test one strategy at a time in a copy and compare results and timings using the same warm-up protocol.']
    rows = []
    for q in queries:
        signals = ' '.join(f'<span class="pill">{label}</span>' for key, label in LABELS.items() if q['flags'][key]) or 'No highlighted signals'
        search = esc(' '.join([q['query_id']] + q['tables']).lower())
        flags = ' '.join(k for k, v in q['flags'].items() if v)
        rows.append(f'<tr data-search="{search}" data-signals="{flags}"><td>{link(q["query_id"])}</td><td>{fmt(q["average_ms"]) + " ms" if q["average_ms"] is not None else "Not measured"}</td><td>{fmt(q["estimated_cost"])}</td><td>{signals}</td><td>{esc(", ".join(q["tables"]))}</td></tr>')
    query_table = f'<p id="query-count" class="muted">{len(queries)} of {len(queries)} queries</p><table><thead><tr><th>Query</th><th>Average time</th><th>Estimated cost</th><th>Signals</th><th>Tables</th></tr></thead><tbody id="query-rows">' + ''.join(rows) + '</tbody></table>'
    scale = payload.get('scale')
    datasets = navigation(report_name(scale or '1GB'))
    replacements = dict(SCALE=esc(' - ' + scale if scale else ''), DATASETS=datasets, STATUS=status, CARDS=cards, BASIS=esc(basis), RANKING=bars or '<p>No valid plans.</p>',
                        CONCENTRATION=concentration, PATTERNS=patterns, STEPS=''.join('<li>' + esc(s) + '</li>' for s in steps),
                        STRATEGIES=strategy_cards(payload.get('strategies', [])), INDEXES=indexes, QUERIES=query_table, DETAILS=details)
    page = template('dashboard.html', replacements)
    if not any(s['actual'] for q in queries for s in q['statements']):
        page = page.replace('<option value="cardinality">Row estimate mismatch</option>', '')
    return page
