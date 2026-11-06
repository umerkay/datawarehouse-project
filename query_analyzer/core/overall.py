"""Cross-dataset plan observations for report figures, with query-level evidence."""
from .common import SCALES
from .strategies import column_name


def overall_analysis(payloads, index_groups):
    by_scale = {payload['scale']: payload for payload in payloads}
    coverage = {scale: len(by_scale[scale]['queries']) for scale in SCALES}
    patterns = {}

    def add(kind, label, scale, evidence):
        pattern = patterns.setdefault((kind, label), dict(kind=kind, label=label, datasets={s: dict(available=coverage[s] > 0, queries=[], evidence=[]) for s in SCALES}))
        data = pattern['datasets'][scale]
        if evidence['query_id'] not in data['queries']:
            data['queries'].append(evidence['query_id'])
        if evidence not in data['evidence']:
            data['evidence'].append(evidence)

    for scale, payload in by_scale.items():
        for finding in payload['strategies']:
            if finding['kind'] in ('columnstore', 'filter'):
                kind = 'scan_aggregate' if finding['kind'] == 'columnstore' else 'filtered_scan'
                for evidence in finding['evidence']:
                    add(kind, finding['table'], scale, evidence)
        for query in payload['queries']:
            for statement in query['statements']:
                for op in statement['operators']:
                    grouping = op.get('grouping', [])
                    ordering = op.get('ordering', [])
                    if grouping and op.get('measures') and all(col.get('Table') and col.get('Column') for col in grouping):
                        label = ', '.join(sorted(column_name(col) for col in grouping))
                        add('grouping', label, scale, dict(query_id=query['query_id'], statement_id=statement['statement_id'], node=op['node'], detail='Aggregate expressions: ' + '; '.join(op['measures'])))
                    if op['physical'] == 'Sort' and ordering and all(col.get('Table') and col.get('Column') for col in ordering):
                        label = ', '.join(column_name(col) + (' ASC' if col['ascending'] == '1' else ' DESC') for col in ordering)
                        add('ordering', label, scale, dict(query_id=query['query_id'], statement_id=statement['statement_id'], node=op['node'], detail='Estimated output rows: ' + str(op['estimated_rows'])))
                for message in statement['findings']:
                    if 'NoJoinPredicate="1"' in message:
                        add('no_join_predicate', query['query_id'], scale, dict(query_id=query['query_id'], statement_id=statement['statement_id'], node=None, detail=message))
    for pattern in patterns.values():
        for data in pattern['datasets'].values():
            data['queries'].sort()
    ordered = sorted(patterns.values(), key=lambda p: (-max(len(d['queries']) for d in p['datasets'].values()), p['label']))
    dates = [group for group in index_groups if len(group['equality']) == 1 and group['equality'][0].endswith('_sold_date_sk]') and not group['ranges']]
    return dict(coverage=coverage, errors={scale: by_scale[scale]['errors'] for scale in SCALES}, date_indexes=dates,
                patterns=ordered, index_groups=index_groups)
