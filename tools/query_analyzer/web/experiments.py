"""Compare recorded baseline/indexed experiments without inferring missing data."""
from collections import Counter
from .common import navigation
from .plan_views import esc, plan_tree


def render(sources):
    sections = []
    for scale, baseline in sources.items():
        indexed = baseline.get('indexed', {})
        plans = indexed.get('plans', {})
        body = ''
        for query in sorted(set(baseline['plans']) | set(plans)):
            before, after = baseline['plans'].get(query), plans.get(query)
            content = ''
            if before and after:
                same_sql = before['sql'] == after['sql']
                content += '<p>Captured SQL: <b>' + ('matching' if same_sql else 'different — do not attribute differences to indexes alone') + '</b>. Structure: ' + ('matching' if before['shape'] == after['shape'] else 'changed') + '.</p>'
                counts = lambda plan: Counter(op['physical'] for stmt in plan['statements'] for op in stmt['operators'])
                old, new = counts(before), counts(after)
                changes = [f'{name}: {old[name]} → {new[name]}' for name in sorted(old.keys() | new.keys()) if old[name] != new[name]]
                content += '<p>Operator count changes: ' + esc('; '.join(changes) or 'none') + '.</p>'
            else:
                content += '<p>Plan comparison pending: both scenarios need a captured plan.</p>'
            first, second = baseline['timings'].get(query), indexed.get('timings', {}).get(query)
            if first is not None and second is not None:
                content += f'<p>Recorded average: {first:,.1f} ms → {second:,.1f} ms.'
                if first > 0:
                    content += f' Duration change: {(second / first - 1) * 100:+.1f}%.'
                content += ' Measurements are separate evidence; verify SQL and benchmark conditions.</p>'
            else:
                content += '<p>Paired timing measurements unavailable.</p>'
            for label, plan in [('Baseline', before), ('Indexed', after)]:
                if plan:
                    content += '<div><h3>' + label + '</h3>' + ''.join(plan_tree(stmt) for stmt in plan['statements']) + '</div>'
            body += '<details><summary>' + esc(query) + '</summary>' + content + '</details>'
        if not indexed:
            body = '<p>Indexed captures pending. Configure indexed_plans and indexed_summary for this dataset to compare both scenarios.</p>'
        sections.append('<section class="panel"><h2>' + esc(scale) + '</h2>' + body + '</section>')
    return '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Baseline / indexed</title><link rel="stylesheet" href="assets/dashboard.css"></head><body><header><h1>Baseline / indexed experiments</h1></header><main>' + navigation('experiments.html') + '<p>Compare the same query and dataset before and after an indexing experiment. Plan changes describe strategy, not measured improvements. Confirm equivalent results and matching settings.</p>' + ''.join(sections) + '</main></body></html>'
