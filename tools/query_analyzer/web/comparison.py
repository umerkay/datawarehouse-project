"""Build a static, offline comparison of SQL Server plans across dataset sizes."""
from collections import Counter
import html
import json
import math

from ..core.common import SCALES
from .common import navigation, report_name, template

def esc(value):
    return html.escape(str(value))


def plan_signals(plan):
    """Use one definition of index candidates and warnings throughout comparison."""
    if plan is None:
        return dict(indexes=None, warnings=None)
    statements = plan['statements']
    candidates = {json.dumps([m['schema'], m['table'], m['columns']], sort_keys=True)
                  for statement in statements for m in statement['missing_indexes']}
    return dict(indexes=len(candidates), warnings=sum(
        'Warnings' in finding for statement in statements for finding in statement['findings']))


def tree_html(tree):
    physical, logical, objects, branches = tree
    label = (physical or 'Unknown operation') + (' / ' + logical if logical and logical != physical else '')
    tables = ', '.join(str(obj[1]) + (' / ' + str(obj[2]) if obj[2] else '') for obj in objects)
    return '<li><span>' + esc(label) + ('<small> ' + esc(tables) + '</small>' if tables else '') + '</span>' + ('<ul>' + ''.join(tree_html(child) for child in branches) + '</ul>' if branches else '') + '</li>'


def duration_chart(query, milliseconds):
    """Inline SVG: quantitative dataset sizes, zero-based seconds, gaps preserved."""
    values = [value / 1000 if value is not None else None for value in milliseconds]
    present = [value for value in values if value is not None]
    if not present:
        return '<p class="empty">No historical timings available for a line chart.</p>'
    maximum = max(present)
    raw_step = maximum / 4 if maximum else 0.25
    magnitude = 10 ** math.floor(math.log10(raw_step))
    step = next(factor * magnitude for factor in (1, 2, 5, 10) if factor * magnitude >= raw_step)
    top_value = max(step, math.ceil(maximum / step) * step)
    left, right, top, bottom = 80, 840, 42, 255
    def label(value):
        return f'{value:.3g}' if 0 < value < 0.001 else (f'{value:,.3f}'.rstrip('0').rstrip('.') or '0')
    coordinates = [(left + (size - 1) / 9 * (right - left),
                    bottom - value / top_value * (bottom - top)) if value is not None else None
                   for size, value in zip((1, 2, 5, 10), values)]
    chart_id = 'duration-' + query
    description = '; '.join(scale + ': ' + (label(value) + ' seconds' if value is not None else 'not available')
                            for scale, value in zip(SCALES, values))
    svg = f'<svg class="duration-chart" viewBox="0 0 900 325" role="img" aria-labelledby="{esc(chart_id)}-title {esc(chart_id)}-desc"><title id="{esc(chart_id)}-title">{esc(query)}: historical duration by dataset size</title><desc id="{esc(chart_id)}-desc">{esc(description)}</desc>'
    ticks = int(round(top_value / step))
    for index in range(ticks + 1):
        value = index * step
        y = bottom - value / top_value * (bottom - top)
        svg += f'<line class="chart-grid" x1="{left}" y1="{y:.2f}" x2="{right}" y2="{y:.2f}"/><text class="chart-tick" x="{left-12}" y="{y+5:.2f}" text-anchor="end">{label(value)}</text>'
    svg += f'<text class="chart-axis-title" x="{left}" y="20">Average duration (seconds)</text>'
    segments, current = [], []
    for point in coordinates:
        if point is None:
            if current:
                segments.append(current)
                current = []
        else:
            current.append(point)
    if current:
        segments.append(current)
    for segment in segments:
        if len(segment) < 2:
            continue
        points = ' '.join(f'{x:.2f},{y:.2f}' for x, y in segment)
        area = f'{segment[0][0]:.2f},{bottom} ' + points + f' {segment[-1][0]:.2f},{bottom}'
        svg += f'<polygon class="chart-area" points="{area}"/><polyline class="chart-line" points="{points}"/>'
    for index, (scale, value, point) in enumerate(zip(SCALES, values, coordinates)):
        x = left + ((1, 2, 5, 10)[index] - 1) / 9 * (right - left)
        svg += f'<text class="chart-tick" x="{x:.2f}" y="{bottom+28}" text-anchor="middle">{scale}</text>'
        if point is None:
            svg += f'<text class="chart-missing" x="{x:.2f}" y="{bottom-12}" text-anchor="middle">No data</text>'
            continue
        _, y = point
        anchor = 'start' if index == 0 else ('end' if index == 3 else 'middle')
        text_y = y-14 if y > top+16 else y+25
        svg += f'<circle class="chart-point" cx="{x:.2f}" cy="{y:.2f}" r="5" data-scale="{scale}" data-seconds="{value}"><title>{scale}: {label(value)} s</title></circle><text class="chart-value" x="{x:.2f}" y="{text_y:.2f}" text-anchor="{anchor}">{label(value)} s</text>'
    svg += f'<text class="chart-axis-title" x="{(left+right)/2}" y="315" text-anchor="middle">Dataset size (GB)</text></svg>'
    return '<figure class="duration-figure"><div class="chart-scroll">' + svg + '</div><figcaption>Historical measured averages. Missing timings break the line; plan availability is separate.</figcaption></figure>'


def workload_data(sources, queries):
    """Keep missing plan evidence distinct from an observed count of zero."""
    rows = []
    for query in queries:
        scales = {}
        for scale in SCALES:
            plan = sources[scale]['plans'].get(query)
            scales[scale] = dict(ms=sources[scale]['timings'].get(query),
                                **plan_signals(plan))
        rows.append(dict(query=query, scales=scales))
    return rows


def plan_matrix(sources, queries):
    rows = []
    for query in queries:
        reference_scale = next((s for s in SCALES if query in sources[s]['plans']), None)
        reference = sources[reference_scale]['plans'][query] if reference_scale else None
        cells, states = [], []
        for scale in SCALES:
            plan = sources[scale]['plans'].get(query)
            state = ('missing' if plan is None else 'reference' if scale == reference_scale else
                     'same' if plan['shape'] == reference['shape'] else 'changed')
            sql_changed = plan is not None and plan['sql'] != reference['sql']
            states.append(state)
            if sql_changed:
                states.append('sql')
            label = dict(missing='No plan', reference='Reference', same='Same structure', changed='Changed structure')[state]
            cells.append(f'<td><a class="plan-cell {state}" data-select="{esc(query)}" href="#compare-{esc(query)}">{label}' +
                         ('<small>SQL differs</small>' if sql_changed else '') + '</a></td>')
        rows.append(f'<tr data-plan-query="{esc(query)}" data-plan-states="{" ".join(states)}"><th scope="row"><a data-select="{esc(query)}" href="#compare-{esc(query)}">{esc(query)}</a></th>' + ''.join(cells) + '</tr>')
    return ''.join(rows)


def operator_chart(sources, query):
    """Compare exact physical operator counts on one shared absolute scale."""
    counts = {}
    for scale in SCALES:
        plan = sources[scale]['plans'].get(query)
        counts[scale] = (Counter(o['physical'] or 'Unknown' for statement in plan['statements']
                                 for o in statement['operators']) if plan else None)
    names = sorted(set().union(*(set(c) for c in counts.values() if c is not None)))
    if not names:
        return '<p>No operator evidence available.</p>'
    maximum = max((n for c in counts.values() if c for n in c.values()), default=1)
    rows = []
    for name in names:
        cells = []
        for scale in SCALES:
            count = counts[scale][name] if counts[scale] is not None else None
            cells.append('<td>' + (f'<div class="operator-value"><span class="operator-track"><span class="operator-bar scale-{scale}" style="width:{count/maximum*100:.2f}%"></span></span><b>{count}</b></div>' if count is not None else '<span class="muted">No plan</span>') + '</td>')
        rows.append('<tr><th scope="row">' + esc(name) + '</th>' + ''.join(cells) + '</tr>')
    return '<div class="operator-scroll"><table class="operator-chart"><caption>Physical operator counts: shared scale from 0 to ' + str(maximum) + '</caption><thead><tr><th>Operator</th>' + ''.join('<th>' + s + '</th>' for s in SCALES) + '</tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>'


def plan_card(sources, query, scale, base, baseline_scale, reference_time):
    """Render one size, including its explicit missing-plan state."""
    plan = sources[scale]['plans'].get(query)
    value = sources[scale]['timings'].get(query)
    card = f'<article class="card"><h3>{scale}</h3><p>Historical average: <b>{f"{value/1000:,.3f} s" if value is not None else "Not available"}</b></p>'
    if value is not None and reference_time is not None and reference_time > 0:
        card += f'<p class="muted">{value/reference_time:.2f}x the 1 GB duration</p>'
    if plan is None:
        card += '<div class="notice">No plan captured for this query and size.</div><p>Timing data alone cannot reveal the execution plan.</p>'
    else:
        target_report = report_name(scale)
        card += f'<p><a href="{target_report}#detail-{esc(query)}">Open full query analysis</a></p>'
        ops = [o for s in plan['statements'] for o in s['operators']]
        counts = Counter(o['physical'] for o in ops)
        signals = plan_signals(plan)
        status = 'Reference plan' if scale == baseline_scale else ('Same plan structure' if plan['shape'] == base['shape'] else 'Different plan structure')
        sql_status = 'Identical captured SQL' if plan['sql'] == base['sql'] else 'Captured SQL differs; compare parameters and text'
        card += f'<span class="badge">{status}</span><p><small>Compared with {baseline_scale}. {sql_status}.</small></p>'
        card += f'<p>Estimated cost: <b>{plan["estimated_cost"]:,.3f}</b><br>{len(ops)} operators · {signals["warnings"]} warnings · {signals["indexes"]} index candidates</p>'
        card += '<p>' + ('Actual runtime counters available' if any(s['actual'] for s in plan['statements']) else 'Estimated plan; no runtime counters') + '</p>'
        mix = [('Scans', sum(n for name, n in counts.items() if 'Scan' in (name or '')), '#108879'),
               ('Seeks', sum(n for name, n in counts.items() if 'Seek' in (name or '')), '#3876b5'),
               ('Hash operators', counts['Hash Match'], '#df8a40'), ('Sorts', counts['Sort'], '#9659a3')]
        mix.append(('Other operators', len(ops) - sum(n for _, n, _ in mix), '#6b8799'))
        card += '<h4>Operation mix</h4><div class="mix">' + ''.join(f'<span title="{name}: {count}" style="flex:{count};background:{color}"></span>' for name, count, color in mix if count) + '</div>'
        card += '<ul>' + ''.join(f'<li><span style="color:{color}">&#9632;</span> {name}: <b>{count}</b></li>' for name, count, color in mix) + '</ul>'
        card += '<details><summary>Plan tree</summary><p class="muted">Parent operations above their inputs. Counts and costs are not durations.</p><div class="tree"><ul>' + ''.join(tree_html(t) for t in plan['trees']) + '</ul></div></details>'
        card += '<details><summary>Captured SQL</summary><pre>' + esc('\n\n'.join(s['sql'] or '' for s in plan['statements'])) + '</pre></details>'
    return card + '</article>'


def query_section(sources, query, values):
    """Compose the charts and four plan cards for a selected query."""
    maximum = max((v for v in values if v is not None), default=0)
    bars = ''.join(f'<div class="barrow"><span>{scale}</span><div class="track"><div class="bar" style="width:{v/maximum*100 if maximum and v is not None else 0:.2f}%"></div></div><b>{f"{v/1000:,.3f} s" if v is not None else "No timing"}</b></div>' for scale, v in zip(SCALES, values))
    base = next((sources[s]['plans'][query] for s in SCALES if query in sources[s]['plans']), None)
    baseline_scale = next((s for s in SCALES if query in sources[s]['plans']), None)
    cards = []
    for scale in SCALES:
        cards.append(plan_card(sources, query, scale, base, baseline_scale, values[0]))
    chart = duration_chart(query, values)
    return (
        f'<section class="query-comparison" id="compare-{esc(query)}" data-query="{esc(query)}">'
        f'<h2>{esc(query)}</h2>'
        '<div class="panel"><h3>How does the execution plan change?</h3>'
        '<p>Compare scans, joins, sorts and other operations across sizes. '
        'Longer bars mean more operators, not more execution time. '
        'Equal counts can still hide different tree structures.</p>'
        f'{operator_chart(sources, query)}'
        '<p class="muted">Expand Plan tree in the cards below to inspect branch order, '
        'tables and indexes. Check the captured SQL before attributing a change to dataset size.</p></div>'
        '<div class="panel"><h3>Runtime growth across dataset sizes</h3>'
        f'{chart}<details><summary>Exact timings and relative bars</summary>{bars}</details>'
        '<p class="muted">Each query uses its own zero-based vertical scale. '
        'Estimated optimizer cost is not plotted as execution time.</p></div>'
        '<div class="plans">' + ''.join(cards) + '</div></section>'
    )


def render(sources):
    """Assemble the page from independently rendered comparison sections."""
    queries = sorted(set().union(*(set(s['plans']) | set(s['timings']) for s in sources.values())))
    coverage, overview, sections = [], [], []
    for scale in SCALES:
        source = sources[scale]
        coverage.append(f'<div class="card"><h2>{scale}</h2><b>{len(source["plans"])} plans</b><p>{len(source["timings"])} historical timings</p><small>Plans: {esc(source["plan_source"])}<br>Timings: {esc(source["timing_source"])}</small></div>')
    for query in queries:
        values = [sources[scale]['timings'].get(query) for scale in SCALES]
        cells = ''.join('<td>' + (f'{v/1000:,.3f} s' if v is not None else 'Not available') + '</td>' for v in values)
        overview.append(f'<tr data-search="{esc(query)}"><td><a href="#compare-{esc(query)}" data-select="{esc(query)}">{esc(query)}</a></td>{cells}</tr>')
        sections.append(query_section(sources, query, values))
    errors = [scale + ': ' + error for scale in SCALES for error in sources[scale]['errors']]
    replacements = dict(NAV=navigation('comparison.html'), COVERAGE=''.join(coverage), OPTIONS=''.join(f'<option value="{esc(q)}">{esc(q)}</option>' for q in queries),
                        QUERIES=''.join(overview), SECTIONS=''.join(sections), PLAN_MATRIX=plan_matrix(sources, queries),
                        WORKLOAD_DATA=json.dumps(workload_data(sources, queries)).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026'),
                        ERRORS=('<details class="notice"><summary>Input errors</summary><pre>' + esc('\n'.join(errors)) + '</pre></details>') if errors else '')
    return template('comparison.html', replacements)
