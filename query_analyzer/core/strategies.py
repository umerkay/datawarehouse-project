"""Evidence-backed optimization hypotheses. Never execute or rewrite SQL."""
import json


MIN_ROWS = 10000


def table_name(obj):
    return '.'.join(value for key in ('Schema', 'Table') if (value := obj.get(key) or obj.get(key.lower())))


def column_name(col):
    return '.'.join(col.get(key, '') for key in ('Schema', 'Table', 'Column') if col.get(key))


def strategies(queries):
    findings = {}

    def add(kind, table, signature, title, origin, evidence, experiment, limitation, query, statement, node=None):
        key = (kind, table, signature)
        finding = findings.setdefault(key, dict(kind=kind, table=table, signature=signature, title=title,
                                                origin=origin, experiment=experiment, limitation=limitation,
                                                evidence=[], queries=set()))
        entry = dict(query_id=query, statement_id=statement, node=node, detail=evidence)
        if entry not in finding['evidence']:
            finding['evidence'].append(entry)
        finding['queries'].add(query)

    for query in queries:
        query_id = query['query_id']
        for statement in query['statements']:
            stmt_id = statement['statement_id']
            operators = {op['node']: op for op in statement['operators']}

            def descendants(op, seen=None):
                seen = set() if seen is None else seen
                result = []
                for node in op.get('children', []):
                    if node in seen or node not in operators:
                        continue
                    seen.add(node)
                    child = operators[node]
                    result.append(child)
                    result.extend(descendants(child, seen))
                return result

            for candidate in statement['missing_indexes']:
                columns = candidate['columns']
                equality = sorted(columns.get('EQUALITY', []))
                ranges = columns.get('INEQUALITY', [])
                signature = json.dumps([equality, ranges])
                detail = 'Equality keys: ' + (', '.join(equality) or 'none') + '; range keys: ' + (', '.join(ranges) or 'none') + '; INCLUDE: ' + (', '.join(columns.get('INCLUDE', [])) or 'none')
                add('index', table_name(candidate), signature, 'Evaluate a shared rowstore index', 'SQL Server missing-index suggestion', detail,
                    'Compare these key requests with existing indexes. Test one candidate for this key pattern; review INCLUDE variants across the listed queries before consolidating.',
                    'Equality keys are grouped without assuming their final order. Range-key order is retained. Suggestions do not prove selectivity or guaranteed benefit; account for storage and load overhead.', query_id, stmt_id)

            for op in operators.values():
                physical = op.get('physical') or ''
                if 'Scan' in physical and op.get('estimated_rows_read') is not None:
                    read, output = op['estimated_rows_read'], op['estimated_rows']
                    residuals = [p['expression'] for p in op.get('predicates', []) if p['role'] == 'Predicate']
                    if read >= MIN_ROWS and output <= read / 10 and residuals:
                        for obj in op['objects']:
                            if not obj.get('Table'):
                                continue
                            add('filter', table_name(obj), '', 'Investigate selective filtering at the scan', 'Analyzer hypothesis',
                                f'Node estimates {read:,.0f} rows read and {output:,.0f} rows output. Residual predicate: ' + '; '.join(dict.fromkeys(residuals)),
                                'Inspect the exact predicate for indexable conditions and implicit conversions. Test a suitable filter/join index or an equivalent predicate rewrite.',
                                'The 10:1 threshold is a screening rule based on estimates, not measured reads. Confirm predicate semantics and actual reads before choosing an index.', query_id, stmt_id, op['node'])

                aggregate = op.get('logical') == 'Aggregate' or physical == 'Stream Aggregate'
                if aggregate:
                    inputs = descendants(op)
                    for scan in inputs:
                        if 'Scan' not in (scan.get('physical') or '') or scan['estimated_rows'] < MIN_ROWS:
                            continue
                        for obj in scan['objects']:
                            if not obj.get('Table') or obj.get('Storage') == 'ColumnStore' or 'Columnstore' in (scan.get('physical') or ''):
                                continue
                            add('columnstore', table_name(obj), '', 'Evaluate columnstore for analytical scans', 'Analyzer hypothesis',
                                f"Scan node {scan['node']} estimates {scan['estimated_rows']:,.0f} output rows and supplies aggregate node {op['node']} ({physical}). Access: {obj.get('Index', 'heap')}; storage: {obj.get('Storage', 'not recorded')}.",
                                'Test a columnstore strategy on this table, retaining a comparable baseline. Evaluate the affected queries together and measure index size and loading overhead.',
                                'A scan feeding an aggregate motivates an experiment; it does not prove columnstore will help. Review scanned columns, filter selectivity and existing columnstore coverage.', query_id, stmt_id, scan['node'])

                    groups = op.get('grouping', [])
                    if groups and op.get('measures') and all(col.get('Table') and col.get('Column') for col in groups):
                        names = sorted(column_name(col) for col in groups)
                        tables = ', '.join(sorted({table_name(col) for col in groups}))
                        add('aggregate', tables, json.dumps(names), 'Investigate reusable pre-aggregations', 'Analyzer hypothesis',
                            'Grouping: ' + ', '.join(names) + '. Measures: ' + ('; '.join(op.get('measures', [])) or 'not resolved'),
                            'Compare filters, joins and aggregate expressions across these queries. If a common grain and compatible measures exist, test a summary table or an eligible indexed view.',
                            'Matching grouping columns alone do not establish a reusable result. Check correctness, refresh cost and indexed-view eligibility; an ordinary view does not materialize results.', query_id, stmt_id, op['node'])

                ordering = op.get('ordering', [])
                if physical == 'Sort' and ordering and all(col.get('Table') and col.get('Column') for col in ordering):
                    names = [column_name(col) + (' ASC' if col['ascending'] == '1' else ' DESC') for col in ordering]
                    tables = ', '.join(sorted({table_name(col) for col in ordering}))
                    add('ordering', tables, json.dumps(names), 'Investigate recurring sort requirements', 'Analyzer hypothesis',
                        'Sort keys: ' + ', '.join(names) + f". Estimated output: {op['estimated_rows']:,.0f} rows.",
                        'Check whether a compatible index order can reach this operator through the joins, or whether an equivalent rewrite can avoid repeated sorting.',
                        'An index on the sort columns does not necessarily remove the sort: joins, parallel exchanges and required ordering can prevent this.', query_id, stmt_id, op['node'])

    result = []
    for finding in findings.values():
        if finding['kind'] in ('aggregate', 'ordering') and len(finding['queries']) < 2:
            continue
        finding['queries'] = sorted(finding['queries'])
        result.append(finding)
    order = {'index': 0, 'filter': 1, 'columnstore': 2, 'aggregate': 3, 'ordering': 4}
    return sorted(result, key=lambda f: (order[f['kind']], -len(f['queries']), f['table'], f['signature']))
