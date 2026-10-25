"""Thin SQLGlot adapter: scoped names, predicate extraction, no CTE lineage."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import Scope, traverse_scope

COMPARISONS = (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE)
OPERATORS = {exp.EQ: "=", exp.NEQ: "<>", exp.GT: ">", exp.GTE: ">=",
             exp.LT: "<", exp.LTE: "<="}
MEASURES = (exp.Sum, exp.Avg, exp.Count, exp.Min, exp.Max, exp.Stddev, exp.StddevPop,
            exp.StddevSamp, exp.Variance, exp.Median, exp.CountIf, exp.ApproxDistinct)


@dataclass(frozen=True)
class Source:
    table: str
    role: str
    reference_type: str
    columns: frozenset[str] | None


@dataclass(frozen=True)
class Endpoint:
    table: str
    column: str
    role: str
    reference_type: str


def table_name(table: exp.Table) -> str:
    return ".".join(part.name.lower() for part in table.parts)


def branches(expression: exp.Expression) -> list[exp.Select]:
    if isinstance(expression, exp.SetOperation):
        return branches(expression.left) + branches(expression.right)
    expression = expression.unnest() if isinstance(expression, exp.Subquery) else expression
    return [expression] if isinstance(expression, exp.Select) else []


def local_columns(node: exp.Expression, select: exp.Expression) -> list[exp.Column]:
    """Columns of `node` owned by `select`, excluding those inside nested subqueries."""
    return [c for c in node.find_all(exp.Column) if c.find_ancestor(exp.Select) is select]


def filter_target(node: exp.Expression, select: exp.Expression) -> tuple[str, exp.Expression] | None:
    """Return (kind, column side) for column-vs-value predicates; joins return None."""
    def has_local(side: exp.Expression | None) -> bool:
        return side is not None and bool(local_columns(side, select))
    if isinstance(node, COMPARISONS):
        left, right = node.left, node.right
        if has_local(left) == has_local(right):
            return None
        kind = {exp.EQ: "equality", exp.NEQ: "not_equal"}.get(type(node), "range")
        return kind, left if has_local(left) else right
    if isinstance(node, exp.Between):
        values = [node.args.get("low"), node.args.get("high")]
        kind, target = "range", node.this
    elif isinstance(node, exp.In):
        values = node.expressions
        kind = "in_subquery" if node.args.get("query") is not None else "in_list"
        target = node.this
    elif isinstance(node, (exp.Like, exp.ILike)):
        kind, target, values = "like", node.this, [node.expression]
    elif isinstance(node, exp.Is):
        kind, target, values = "is_null", node.this, [node.expression]
    else:
        return None
    if not has_local(target) or any(has_local(v) for v in values):
        return None
    return kind, target


def collect_usage(scope: Scope, scope_id: str, resolve_all, usage: dict[str, list]) -> None:
    """Record GROUP BY sets, column-vs-value filters and aggregate arguments for one SELECT scope."""
    select = scope.expression
    referenced, _, _ = resolve_all(select, scope)
    usage["referenced"].append({"scope_id": scope_id, "columns": referenced})
    group = select.args.get("group")
    if group is not None:
        columns, complete, on_aggregate = resolve_all(group, scope, False)
        usage["group_by"].append({
            "scope_id": scope_id, "columns": columns, "complete": complete,
            "on_aggregate": on_aggregate,
            "rollup": group.find(exp.Rollup, exp.Cube, exp.GroupingSets) is not None
            or bool(group.args.get("rollup") or group.args.get("cube")),
            "raw": group.sql(dialect="tsql"),
        })
    roots = [select.args["where"].this] if select.args.get("where") else []
    roots += [j.args["on"] for j in select.args.get("joins") or [] if j.args.get("on") is not None]
    for root in roots:
        for node in root.walk():
            if node.find_ancestor(exp.Select) is not select:
                continue
            found = filter_target(node, select)
            if found is None:
                continue
            kind, target = found
            columns, complete, on_aggregate = resolve_all(target, scope, False)
            usage["filters"].append({
                "scope_id": scope_id, "columns": columns, "complete": complete,
                "kind": "aggregate_value" if on_aggregate else kind,
                "expression": not isinstance(target.unnest(), exp.Column),
                "negated": isinstance(node.parent, exp.Not),
                "conditional": node.find_ancestor(exp.Or) is not None,
                "raw": node.sql(dialect="tsql"),
            })
    for aggregate in select.find_all(MEASURES):
        if aggregate.find_ancestor(exp.Select) is not select:
            continue
        columns, complete, _ = resolve_all(aggregate, scope)
        usage["aggregates"].append({
            "scope_id": scope_id, "function": aggregate.key, "columns": columns,
            "complete": complete, "window": isinstance(aggregate.parent, exp.Window),
            "raw": aggregate.sql(dialect="tsql"),
        })


def read_schema(path: Path | None) -> dict[str, frozenset[str]]:
    if path is None:
        return {}
    schema = {}
    for statement in sqlglot.parse(path.read_text(encoding="utf-8"), read="tsql"):
        if isinstance(statement, exp.Create) and isinstance(statement.this, exp.Schema):
            definition = statement.this
            if isinstance(definition.this, exp.Table):
                schema[table_name(definition.this)] = frozenset(
                    c.name.lower() for c in definition.expressions
                    if isinstance(c, exp.ColumnDef)
                )
    if not schema:
        raise ValueError(f"No CREATE TABLE definitions found in schema: {path}")
    return schema


def extract(sql: str, query_id: str, schema: dict[str, frozenset[str]]) -> dict[str, Any]:
    statements = sqlglot.parse(sql, read="tsql", error_level=sqlglot.ErrorLevel.RAISE)
    if not any(isinstance(s, exp.Query) for s in statements):
        raise ValueError("No SELECT query found")
    if any(s is not None and not isinstance(s, exp.Query) for s in statements):
        raise ValueError("Only SELECT / WITH query statements are supported")
    report: dict[str, Any] = {
        "query_id": query_id, "status": "success", "original_sql": sql,
        "parser_sql": [s.sql(dialect="tsql") for s in statements if s is not None],
        "references": [], "joins": [], "unresolved": [],
        "usage": {"group_by": [], "filters": [], "aggregates": [], "referenced": []},
    }
    for statement_index, statement in enumerate(statements):
        if statement is None:
            continue
        scopes = list(traverse_scope(statement))
        scope_ids = {id(scope): f"s{statement_index}.{i}" for i, scope in enumerate(scopes)}
        scope_by_expression = {id(scope.expression): scope for scope in scopes}
        role_scopes: dict[str, Scope] = {}
        source_maps: dict[int, dict[str, Source]] = {}
        for scope in scopes:
            sources = {}
            for alias, (node, source) in scope.selected_sources.items():
                role = f"{scope_ids[id(scope)]}:{alias.lower()}"
                if isinstance(source, exp.Table):
                    name = table_name(source)
                    columns = schema.get(name)
                    if columns is None and name.startswith("dbo."):
                        columns = schema.get(name[4:])
                    item = Source(name, role, "physical", columns)
                else:
                    kind = "cte" if isinstance(node, exp.Table) else "derived"
                    name = node.name.lower() if isinstance(node, exp.Table) else alias.lower()
                    # Local identity prevents unrelated CTEs in different queries from merging.
                    identity = scope_ids.get(id(source), role)
                    columns = frozenset(c.lower() for c in source.expression.named_selects)
                    if source.outer_columns:
                        columns = frozenset(c.lower() for c in source.outer_columns)
                    item = Source(f"@{query_id}:{identity}:{kind}:{name}", role,
                                  "non_physical", columns if "*" not in columns else None)
                    role_scopes[role] = source
                sources[alias.lower()] = item
                report["references"].append({
                    "table": item.table, "role": role, "reference_type": item.reference_type,
                    "resolution_status": "exact" if item.reference_type == "physical" else "unresolved",
                })
            source_maps[id(scope)] = sources

        def resolve(column: exp.Column, scope: Scope) -> tuple[Endpoint | None, str]:
            current: Scope | None = scope
            while current is not None:
                sources = source_maps.get(id(current), {})
                if column.table:
                    qualifier = column.table.lower()
                    if column.db:
                        qualifier = f"{column.db.lower()}.{qualifier}"
                    candidates = [s for a, s in sources.items()
                                  if qualifier == a or qualifier == s.table]
                else:
                    candidates = [s for s in sources.values()
                                  if s.columns is None or column.name.lower() in s.columns]
                if len(candidates) > 1:
                    return None, f"Ambiguous column {column.sql(dialect='tsql')}"
                if candidates:
                    source = candidates[0]
                    if source.columns is not None and column.name.lower() not in source.columns:
                        return None, f"Unknown column {column.name} on {source.table}"
                    return Endpoint(source.table, column.name.lower(), source.role,
                                    source.reference_type), ""
                if not current.can_be_correlated:
                    break
                current = current.parent
            return None, f"No scoped source for {column.sql(dialect='tsql')}"

        def physical(column: exp.Column, scope: Scope, through_aggregates: bool,
                     depth: int = 0) -> tuple[list[tuple[str, str]], bool, bool]:
            """Resolve to physical columns via simple CTE/derived projections.

            Returns (columns, complete, stopped_at_aggregate)."""
            endpoint, _ = resolve(column, scope)
            if endpoint is None:
                return [], False, False
            if endpoint.reference_type == "physical":
                return [(endpoint.table, endpoint.column)], True, False
            source = role_scopes.get(endpoint.role)
            if source is None or depth >= 6:
                return [], False, False
            selects = branches(source.expression)
            names = [c.lower() for c in (source.outer_columns or
                                         (selects[0].named_selects if selects else []))]
            if endpoint.column not in names:
                return [], False, False
            position, found, complete, stopped = names.index(endpoint.column), [], True, False
            for select in selects:
                inner = scope_by_expression.get(id(select))
                if inner is None or position >= len(select.expressions):
                    complete = False
                    continue
                projection = select.expressions[position].unalias()
                if not through_aggregates and any(
                        a.find_ancestor(exp.Select) is select for a in projection.find_all(MEASURES)):
                    stopped = True
                    continue
                columns = local_columns(projection, select)
                for inner_column in columns:
                    resolved, ok, inner_stopped = physical(inner_column, inner, through_aggregates, depth + 1)
                    found.extend(resolved)
                    complete = complete and ok
                    stopped = stopped or inner_stopped
            return found, complete, stopped

        def resolve_all(node: exp.Expression, scope: Scope,
                        through_aggregates: bool = True) -> tuple[list[dict[str, str]], bool, bool]:
            found, complete, stopped = [], True, False
            for column in local_columns(node, scope.expression):
                resolved, ok, inner_stopped = physical(column, scope, through_aggregates)
                found.extend(resolved)
                complete = complete and ok
                stopped = stopped or inner_stopped
            return [{"table": t, "column": c} for t, c in sorted(set(found))], complete, stopped

        for scope in scopes:
            if isinstance(scope.expression, exp.Select):
                collect_usage(scope, scope_ids[id(scope)], resolve_all, report["usage"])

        for scope in scopes:
            if not isinstance(scope.expression, exp.Select):
                continue
            scope_id = scope_ids[id(scope)]

            def unresolved(predicate: exp.Expression, reason: str) -> None:
                report["unresolved"].append({
                    "query_id": query_id, "scope_id": scope_id,
                    "raw_predicate": predicate.sql(dialect="tsql"),
                    "left_reference": predicate.left.sql(dialect="tsql")
                    if isinstance(predicate, COMPARISONS) else "",
                    "right_reference": predicate.right.sql(dialect="tsql")
                    if isinstance(predicate, COMPARISONS) else "",
                    "reason": reason,
                })

            def add_join(left: Endpoint, right: Endpoint, operator: str,
                         predicate: exp.Expression, join_type: str) -> None:
                status = ("exact" if left.reference_type == right.reference_type == "physical"
                          else "unresolved")
                if not all(e.role.startswith(scope_id + ":") for e in (left, right)):
                    join_type = "UNKNOWN"
                report["joins"].append({
                    "query_id": query_id, "scope_id": scope_id,
                    "left": asdict(left), "right": asdict(right), "operator": operator,
                    "relationship_class": "equality" if operator == "=" else
                    ("cross" if operator == "CROSS" else "non_equality"),
                    "join_type": join_type, "resolution_status": status,
                    "self_join": left.table == right.table,
                    "conditional": predicate.find_ancestor(exp.Or, exp.Not) is not None,
                    "raw_predicate": predicate.sql(dialect="tsql"),
                })
                if status != "exact":
                    unresolved(predicate, "CTE/derived endpoint retained; physical lineage not resolved")

            joins = scope.expression.args.get("joins") or []
            where = scope.expression.args.get("where")
            predicates = [(where.this, "UNKNOWN" if any(j.side for j in joins) else "INNER")] if where else []
            for join in joins:
                kind = join.side or join.kind or "INNER"
                if join.args.get("on") is not None:
                    predicates.append((join.args["on"], kind))
                elif join.kind == "CROSS" and isinstance(join.this, exp.Table):
                    local = source_maps[id(scope)]
                    right_source = local.get(join.this.alias_or_name.lower())
                    from_clause = scope.expression.args.get("from_")
                    if len(joins) == 1 and from_clause is not None:
                        left_source = local.get(from_clause.this.alias_or_name.lower())
                    else:
                        left_source = None
                    if left_source and right_source:
                        add_join(Endpoint(left_source.table, "", left_source.role, left_source.reference_type),
                                 Endpoint(right_source.table, "", right_source.role, right_source.reference_type),
                                 "CROSS", join, "CROSS")
                    else:
                        unresolved(join, "CROSS JOIN with complex left input; no pair inferred")
                elif join.args.get("using") or isinstance(join.this, exp.Lateral):
                    unresolved(join, "USING/APPLY join is not supported by this adapter")

            for predicate, join_type in predicates:
                for comparison in predicate.walk():
                    if comparison.find_ancestor(exp.Select) is not scope.expression:
                        continue
                    if isinstance(comparison, exp.In) and comparison.args.get("query") is not None:
                        unresolved(comparison, "IN subquery membership is not expanded into a physical join")
                        continue
                    if isinstance(comparison, exp.Between):
                        if any(comparison.args[k].find(exp.Column) for k in ("low", "high")):
                            unresolved(comparison, "Column-based BETWEEN requires expression analysis")
                        continue
                    if not isinstance(comparison, COMPARISONS):
                        continue
                    left, right = comparison.left.unnest(), comparison.right.unnest()
                    if not (left.find(exp.Column) and right.find(exp.Column)):
                        continue
                    if not isinstance(left, exp.Column) or not isinstance(right, exp.Column):
                        unresolved(comparison, "Expression/subquery comparison; direct column endpoints required")
                        continue
                    left_endpoint, left_reason = resolve(left, scope)
                    right_endpoint, right_reason = resolve(right, scope)
                    if left_endpoint is None or right_endpoint is None:
                        unresolved(comparison, "; ".join(r for r in (left_reason, right_reason) if r))
                    elif left_endpoint.role != right_endpoint.role:
                        add_join(left_endpoint, right_endpoint, OPERATORS[type(comparison)],
                                 comparison, join_type)
    if report["unresolved"]:
        report["status"] = "partial"
    return report
