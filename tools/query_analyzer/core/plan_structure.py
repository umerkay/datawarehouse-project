"""Compare operator topology independently of row estimates and database names."""
import hashlib
import json

from .analysis import NS, read_plan_documents


def children(node):
    """Nearest descendant RelOps, preserving branch order and topology."""
    found = []
    for child in node:
        if child.tag == '{' + NS['p'] + '}RelOp':
            found.append(child)
        else:
            found.extend(children(child))
    return found


def shape(node):
    objects = []
    for obj in node.findall('./*/p:Object', NS):
        index = obj.get('Index', '')
        if index.startswith('[PK__'):
            index = '[primary key]'
        objects.append([obj.get('Schema'), obj.get('Table'), index, obj.get('Storage')])
    return [node.get('PhysicalOp'), node.get('LogicalOp'), objects,
            [shape(child) for child in children(node)]]


def structure(path, documents=None):
    trees, sql = [], []
    for document in read_plan_documents(path) if documents is None else documents:
        for stmt in document.findall('.//p:StmtSimple', NS):
            plan = stmt.find('p:QueryPlan', NS)
            if plan is not None:
                sql.append(stmt.get('StatementText', '').replace('\r\n', '\n').strip().rstrip(';'))
                trees.extend(shape(root) for root in children(plan))
    digest = lambda value: hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
    return dict(shape=digest(trees), sql=digest(sql), trees=trees)
