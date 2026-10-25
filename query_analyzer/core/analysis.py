#!/usr/bin/env python3
"""Offline SQL Server Showplan analysis. Standard library only; never executes SQL."""
import math
import re
from pathlib import Path
import xml.etree.ElementTree as ET


NS = {'p': 'http://schemas.microsoft.com/sqlserver/2004/07/showplan'}


def number(element, key):
    value = float(element.get(key, '0'))
    if not math.isfinite(value) or value < 0:
        raise ValueError(f'Invalid nonnegative plan number: {key}')
    return value


def read_plan_documents(path, raw=None):
    path = Path(path)
    raw = path.read_bytes() if raw is None else raw
    text = raw.decode('utf-16' if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig')
    # sqlcmd -w 65535 inserts hard wraps, even inside XML names/entities.
    # Remove only wraps at that exact width; preserve normal XML formatting.
    text = ''.join(line.rstrip('\r\n') if len(line.rstrip('\r\n')) == 65535 else line
                   for line in text.splitlines(keepends=True))
    documents = re.findall(r'<(?:[\w.-]+:)?ShowPlanXML\b.*?</(?:[\w.-]+:)?ShowPlanXML>', text, re.S)
    if not documents:
        raise ValueError('No ShowPlanXML document found')
    return [ET.fromstring(document) for document in documents]


def analyze(path, documents=None):
    path = Path(path)
    statements = []
    for root in read_plan_documents(path) if documents is None else documents:
        for stmt in root.findall('.//p:StmtSimple', NS):
            plan = stmt.find('p:QueryPlan', NS)
            if plan is None:
                continue
            operators = []
            findings = []
            for op in plan.findall('.//p:RelOp', NS):
                counters = op.findall('p:RunTimeInformation/p:RunTimeCountersPerThread', NS)
                actual = sum(number(c, 'ActualRows') for c in counters) if counters else None
                executions = sum(number(c, 'ActualExecutions') for c in counters) if counters else None
                estimate = number(op, 'EstimateRows')
                # ActualRows is cumulative across executions; compare per invocation.
                per_execution = actual / executions if executions else actual
                ratio = None
                # Multiple worker counters do not imply repeated serial invocations.
                # Avoid a false mismatch caused by dividing a parallel scan by DOP.
                active_workers = sum(number(c, 'ActualExecutions') > 0 for c in counters)
                if per_execution is not None and active_workers <= 1:
                    low, high = sorted((estimate, per_execution))
                    ratio = high / low if low else (None if high == 0 else 'infinite')
                objects = [dict(o.attrib) for o in op.findall('./*/p:Object', NS)]
                record = dict(node=op.get('NodeId'), physical=op.get('PhysicalOp'),
                              logical=op.get('LogicalOp'), estimated_rows=estimate,
                              actual_rows=actual, actual_executions=executions,
                              cardinality_ratio=ratio, parallel_counters=active_workers > 1, objects=objects,
                              local_cost=number(op, 'EstimateIO') + number(op, 'EstimateCPU'))
                operators.append(record)
                label = f"Node {record['node']} ({record['physical']})"
                if 'Scan' in (record['physical'] or '') and estimate >= 10000:
                    findings.append(label + ': large scan; assess selectivity and index coverage. A scan may be appropriate for analytics.')
                if ratio == 'infinite' or isinstance(ratio, (int, float)) and ratio >= 10:
                    findings.append(label + ': cardinality mismatch >=10x; review statistics and predicates before adding indexes.')
                warnings = op.find('p:Warnings', NS)
                if warnings is not None:
                    findings.append(label + ': ' + ET.tostring(warnings, encoding='unicode'))
                if record['physical'] in ('Sort', 'Hash Match', 'Table Spool', 'Index Spool'):
                    findings.append(label + ': inspect input volume and memory with an actual plan; this operator alone is not evidence of a performance problem.')
            missing = []
            for group in plan.findall('.//p:MissingIndexGroup', NS):
                for index in group.findall('p:MissingIndex', NS):
                    columns = {}
                    for cg in index.findall('p:ColumnGroup', NS):
                        usage = cg.get('Usage')
                        names = [c.get('Name') for c in cg.findall('p:Column', NS)]
                        if usage not in ('EQUALITY', 'INEQUALITY', 'INCLUDE') or any(not name for name in names):
                            raise ValueError('Invalid missing-index column group')
                        columns[usage] = names
                    missing.append(dict(database=index.get('Database'), schema=index.get('Schema'),
                                        table=index.get('Table'), impact=number(group, 'Impact'), columns=columns))
            timing = plan.find('p:QueryTimeStats', NS)
            statements.append(dict(statement_id=stmt.get('StatementId'), sql=stmt.get('StatementText'),
                                   estimated_cost=number(stmt, 'StatementSubTreeCost'),
                                   actual=any(o['actual_rows'] is not None for o in operators),
                                   runtime=dict(timing.attrib) if timing is not None else None,
                                   operators=sorted(operators, key=lambda x: x['local_cost'], reverse=True),
                                   missing_indexes=missing, findings=findings))
    if not statements:
        raise ValueError('No statement with a QueryPlan found')
    return dict(query_id=path.stem, source=str(path), statements=statements,
                estimated_cost=sum(s['estimated_cost'] for s in statements))
