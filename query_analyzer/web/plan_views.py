"""Offline decision views for recorded plans and optional index inventories."""
import html


def esc(value):
    return html.escape(str(value))


def plan_tree(statement):
    operators = {o['node']: o for o in statement['operators']}
    def branch(node_id, ancestors):
        if node_id in ancestors or node_id not in operators:
            return ''
        op = operators[node_id]
        names = ', '.join(' '.join(obj.get(k, '') for k in ('Table', 'Index')) for obj in op['objects'])
        title = f"Node {node_id}: {op['physical']} — {names}"
        body = f'<p>Estimated output: {op["estimated_rows"]:,.0f} rows · Local cost: {op["local_cost"]:,.3f}</p>'
        for predicate in op.get('predicates', []):
            body += '<p><b>' + esc(predicate['role']) + ':</b> <code>' + esc(predicate['expression']) + '</code></p>'
        if op.get('output_columns'):
            body += '<p><b>Output columns:</b> ' + esc(', '.join(op['output_columns'])) + '</p>'
        children = ''.join(branch(child, ancestors | {node_id}) for child in op.get('children', []))
        return '<li><details open><summary>' + esc(title) + '</summary>' + body + ('<ul>' + children + '</ul>' if children else '') + '</details></li>'
    roots = statement.get('roots', [])
    if not roots:
        return ''
    return '<details class="plan-tree"><summary>Execution plan tree and predicates</summary><p class="muted">Root produces the result; nested branches supply its input. Costs and rows are estimates. Expand nodes to inspect access predicates and output columns.</p><ul>' + ''.join(branch(root, set()) for root in roots) + '</ul></details>'


def index_review(candidates, inventory):
    if inventory is None:
        return '<p class="muted">Existing-index inventory unavailable. Export the index catalogue and configure indexes_csv before checking coverage.</p>'
    rows = []
    for candidate in candidates:
        relevant = [i for i in inventory if i['schema'] == candidate['schema'] and i['table'] == candidate['table']]
        equality = candidate['columns'].get('EQUALITY', [])
        keys = equality + candidate['columns'].get('INEQUALITY', [])
        needed = set(keys + candidate['columns'].get('INCLUDE', []))
        matches = []
        for index in relevant:
            prefix = index['keys'][:len(keys)]
            leading = len(prefix) == len(keys) and set(prefix[:len(equality)]) == set(equality) and prefix[len(equality):] == keys[len(equality):]
            covered = needed <= set(index['keys'] + index['includes'])
            eligible = not index['filter'] and index['type'] in ('CLUSTERED', 'NONCLUSTERED')
            label = 'Potential column coverage' if leading and covered and eligible else 'Review overlap / access method'
            matches.append(esc(index['name']) + ': ' + label + '<br><small>Keys: ' + esc(', '.join(index['keys'])) + '; INCLUDE: ' + esc(', '.join(index['includes'])) + '; type: ' + esc(index['type']) + '; filter: ' + esc(index['filter'] or 'none') + '</small>')
        rows.append('<tr><td>' + esc(candidate['table']) + '<br>' + esc(', '.join(keys)) + '</td><td>' + ('<hr>'.join(matches) or 'No enabled index recorded for this table') + '</td></tr>')
    return '<details><summary>Compare suggestions with existing indexes</summary><p class="muted">Column coverage is a screening rule, not proof of equivalent indexes or performance. Review key order, filters and access methods before testing.</p><table><tr><th>Candidate</th><th>Existing indexes</th></tr>' + ''.join(rows) + '</table></details>'
