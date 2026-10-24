#!/usr/bin/env python3
"""Collect estimated plans, or explicitly execute SELECTs to capture actual plans."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from analyze_workload import ROOT, discover
from plan_report import generate, split_documents
from report import stable_id, write_json

LOGGER = logging.getLogger("join_graph.plans")
# The password is expanded only inside the container and never returned or put in argv.
REMOTE = (
    'export SQLCMDPASSWORD="$MSSQL_SA_PASSWORD"; '
    'exec timeout --signal=TERM --kill-after=5s "$1" '
    '/opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -C -b -r 1 '
    '-l 10 -t "$2" -y 0 -w 65535 -x -d "$3"'
)


def sqlcmd(container: str, database: str, sql: str, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "exec", "-i", container, "bash", "-c", REMOTE, "showplan",
         str(timeout + 5), str(timeout), database],
        input=sql, text=True, encoding="utf-8", capture_output=True, timeout=timeout + 20,
        check=False,
    )


def validate_query(sql: str) -> None:
    # sqlcmd directives are processed before T-SQL and must not escape the SHOWPLAN session.
    if re.search(r"(?im)^\s*(?:GO(?:\s|;|$)|:|!!)", sql):
        raise ValueError("sqlcmd directives / GO are not allowed in a query file")
    statements = sqlglot.parse(sql, read="tsql", error_level=sqlglot.ErrorLevel.RAISE)
    if not statements or any(not isinstance(s, exp.Query) for s in statements if s is not None):
        raise ValueError("Only SELECT / WITH query files are allowed")
    if not any(isinstance(s, exp.Query) for s in statements):
        raise ValueError("No SELECT query found")
    if any(s.find(exp.Into) is not None for s in statements if s is not None):
        raise ValueError("SELECT INTO is not allowed")


def estimated_batch(sql: str) -> str:
    validate_query(sql)
    return f"SET NOCOUNT ON;\nGO\nSET SHOWPLAN_XML ON;\nGO\n{sql}\nGO\n"


def actual_batch(sql: str) -> str:
    validate_query(sql)
    return f"SET NOCOUNT ON;\nSET STATISTICS XML ON;\nGO\n{sql}\nGO\n"


def read_context(container: str, database: str, timeout: int) -> dict[str, Any]:
    statement = """
SET NOCOUNT ON;
SET QUOTED_IDENTIFIER ON;
SELECT
  CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(128)) AS server_version,
  CAST(SERVERPROPERTY('Edition') AS nvarchar(128)) AS edition,
  DB_NAME() AS database_name,
  d.compatibility_level, d.collation_name, d.is_auto_update_stats_on,
  JSON_QUERY((
    SELECT s.name AS schema_name, t.name AS table_name, i.name AS index_name,
           i.type_desc, i.is_disabled,
           STUFF((SELECT ',' + c.name
                  FROM sys.index_columns ic JOIN sys.columns c
                    ON c.object_id=ic.object_id AND c.column_id=ic.column_id
                  WHERE ic.object_id=i.object_id AND ic.index_id=i.index_id
                    AND ic.key_ordinal>0 ORDER BY ic.key_ordinal
                  FOR XML PATH(''), TYPE).value('.', 'nvarchar(max)'),1,1,'') AS key_columns
    FROM sys.indexes i JOIN sys.tables t ON i.object_id=t.object_id
    JOIN sys.schemas s ON t.schema_id=s.schema_id
    WHERE t.is_ms_shipped=0 AND i.index_id>0
    ORDER BY s.name,t.name,i.index_id FOR JSON PATH
  )) AS indexes,
  JSON_QUERY((
    SELECT s.name AS schema_name,t.name AS table_name,st.name AS statistics_name,
           STATS_DATE(st.object_id,st.stats_id) AS last_updated
    FROM sys.stats st JOIN sys.tables t ON st.object_id=t.object_id
    JOIN sys.schemas s ON t.schema_id=s.schema_id
    WHERE t.is_ms_shipped=0 ORDER BY s.name,t.name,st.stats_id FOR JSON PATH
  )) AS [statistics]
FROM sys.databases d WHERE d.database_id=DB_ID()
FOR JSON PATH, WITHOUT_ARRAY_WRAPPER;
GO
"""
    result = sqlcmd(container, database, statement, timeout)
    if result.returncode:
        raise ValueError(f"Cannot read database context: {result.stderr.strip() or result.stdout.strip()}")
    # sqlcmd can wrap long text rows; this metadata contains no literal newlines.
    return json.loads("".join(result.stdout.splitlines()))


def collect(input_dir: Path, output: Path, container: str, database: str,
            timeout: int, query_list: Path | None = None, actual: bool = False) -> dict[str, Any]:
    input_dir, output = input_dir.resolve(), output.resolve()
    if output == input_dir or output.is_relative_to(input_dir):
        raise ValueError("Plan output must be outside the input tree")
    selected, selection = discover(input_dir, query_list)
    context = read_context(container, database, timeout)
    output.mkdir(parents=True, exist_ok=True)
    raw_dir = output / "raw_plans"
    raw_dir.mkdir(exist_ok=True)
    manifest = {
        "mode": "actual_STATISTICS_XML" if actual else "estimated_only_SHOWPLAN_XML",
        "database": database, "container": container,
        "collected_at_utc": datetime.now(timezone.utc).isoformat(),
        "per_query_timeout_seconds": timeout, "selection": selection,
        "context": context, "queries": [],
    }
    for index, (query_id, path) in enumerate(selected, 1):
        entry: dict[str, Any] = {"query_id": query_id, "status": "failed", "plan_files": []}
        base = stable_id(query_id)
        started = time.monotonic()
        try:
            raw = path.read_bytes()
            sql = raw.decode("utf-8-sig")
            entry["source_sha256"] = hashlib.sha256(raw).hexdigest()
            entry["original_sql"] = sql
            batch = actual_batch(sql) if actual else estimated_batch(sql)
            result = sqlcmd(container, database, batch, timeout)
            entry["client_elapsed_ms"] = (time.monotonic() - started) * 1000
            # Query result rows are not analysis artifacts; retain only plans and diagnostics.
            stdout = result.stdout if not actual else "\n".join(
                re.findall(r"<ShowPlanXML\b.*?</ShowPlanXML>", result.stdout.replace("\n", ""), flags=re.S))
            (raw_dir / f"{base}.stdout.txt").write_text(stdout, encoding="utf-8")
            (raw_dir / f"{base}.stderr.txt").write_text(result.stderr, encoding="utf-8")
            entry["stdout"] = f"raw_plans/{base}.stdout.txt"
            entry["stderr"] = f"raw_plans/{base}.stderr.txt"
            entry["sqlcmd_exit_code"] = result.returncode
            # Some ODBC sqlcmd builds return exit 0 with "Timeout expired" on stderr.
            timed_out = result.returncode in (124, 137) or "timeout" in result.stderr.lower()
            if result.returncode or timed_out:
                entry["failure_kind"] = "timeout" if timed_out else "sqlcmd_error"
                raise ValueError(f"sqlcmd exit {result.returncode}: {result.stderr.strip() or 'see raw output'}")
            documents = split_documents(result.stdout)
            for number, document in enumerate(documents):
                filename = f"raw_plans/{base}.{number}.sqlplan"
                (output / filename).write_text(document + "\n", encoding="utf-8")
                entry["plan_files"].append(filename)
            entry["status"] = "collected"
        except (OSError, UnicodeError, SqlglotError, ValueError, subprocess.TimeoutExpired) as error:
            entry["client_elapsed_ms"] = (time.monotonic() - started) * 1000
            if isinstance(error, subprocess.TimeoutExpired):
                entry["failure_kind"] = "timeout"
            entry["error"] = str(error)
            LOGGER.error("%s: %s", query_id, error)
        manifest["queries"].append(entry)
        LOGGER.info("[%d/%d] %s: %s", index, len(selected), query_id, entry["status"])
        # Checkpoint after each independent query, including failures.
        write_json(output / "plan_manifest.json", manifest)
    if not selected:
        write_json(output / "plan_manifest.json", manifest)
    return generate(output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "data/1GB/queryset")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--container", default="tpcds_sqlserver")
    parser.add_argument("--query-list", type=Path)
    parser.add_argument("--timeout", type=int, default=20, help="Per-query compile/cancel timeout, seconds")
    parser.add_argument("--actual", action="store_true",
                        help="EXECUTE SELECTs once using STATISTICS XML; default only compiles estimated plans")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.timeout <= 300:
        parser.error("--timeout must be between 1 and 300 seconds")
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s: %(message)s")
    try:
        if args.actual:
            LOGGER.warning("Executing workload SELECTs once with %ds per-query timeout; not a benchmark run",
                           args.timeout)
        data = collect(args.input, args.output, args.container, args.database, args.timeout,
                       args.query_list, args.actual)
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        LOGGER.error("%s", error)
        return 2
    print(json.dumps({key: data["coverage"][key] for key in (
        "discovered_queries", "parsed_queries", "failed_queries", "physical_tables",
        "physical_column_relationships", "join_operator_count", "mapped_join_operator_count",
        "unmapped_join_operator_count",
    )}, indent=2))
    print(f"Plan graph: {args.output.resolve() / 'join_graph.html'}")
    return int(data["coverage"]["failed_queries"] > 0)


if __name__ == "__main__":
    sys.exit(main())
