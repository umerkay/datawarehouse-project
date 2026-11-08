"""Compare recorded missing-index requests, without predicting index benefit."""
from collections import defaultdict
from .common import SCALES


def compare_indexes(sources):
    groups = {}
    for scale in SCALES:
        for query_id, plan in sources[scale]['plans'].items():
            for statement in plan['statements']:
                for index in statement['missing_indexes']:
                    columns = index['columns']
                    equality = tuple(sorted(set(columns.get('EQUALITY', []))))
                    ranges = tuple(columns.get('INEQUALITY', []))
                    key = (index['schema'], index['table'], equality, ranges)
                    group = groups.setdefault(key, dict(schema=key[0], table=key[1], equality=list(equality), ranges=list(ranges), requests=defaultdict(lambda: defaultdict(set))))
                    included = tuple(sorted(set(columns.get('INCLUDE', [])) - set(equality) - set(ranges)))
                    group['requests'][scale][query_id].add(included)
    result = []
    for group in groups.values():
        datasets = {}
        for scale in SCALES:
            requests = group['requests'][scale]
            variants = sorted({variant for values in requests.values() for variant in values}, key=lambda v: (len(v), v))
            union = sorted({col for variant in variants for col in variant})
            variants_out = []
            for variant in variants:
                available = set(variant)
                # Every recorded request for a query must fit, not merely one branch.
                covered = sorted(q for q, values in requests.items() if all(set(value) <= available for value in values))
                exact = sorted(q for q, values in requests.items() if variant in values)
                variants_out.append(dict(columns=list(variant), exact_queries=exact, covered_queries=covered))
            frequencies = [dict(column=col, queries=sorted(q for q, values in requests.items() if any(col in value for value in values))) for col in union]
            frequencies.sort(key=lambda item: (-len(item['queries']), item['column']))
            datasets[scale] = dict(available=bool(sources[scale]['plans']), queries=sorted(requests), variants=variants_out,
                                   union=union, frequencies=frequencies,
                                   common=sorted(set.intersection(*(set(v) for v in variants))) if variants else [])
        result.append({key: value for key, value in group.items() if key != 'requests'} | dict(datasets=datasets))
    return sorted(result, key=lambda g: (-max(len(d['queries']) for d in g['datasets'].values()), g['table'], g['equality'], g['ranges']))
