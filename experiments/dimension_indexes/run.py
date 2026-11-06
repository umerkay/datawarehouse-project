"""Run the dimension experiment using Python's standard library and Docker/sqlcmd."""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import re
import subprocess
import time
import uuid
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SCENARIOS = ('baseline', 'all_dimensions')
DEFAULT_SCALES = ('1GB', '2GB', '5GB')
FACTS = ('store_sales', 'catalog_sales', 'web_sales', 'store_returns',
         'catalog_returns', 'web_returns', 'inventory')
INDEXES = {
    'customer': ('DIMEXP_customer_addr',),
    'customer_address': ('DIMEXP_address_state', 'DIMEXP_address_gmt'),
    'customer_demographics': ('DIMEXP_demo_education', 'DIMEXP_demo_marital'),
    'date_dim': ('DIMEXP_date_year', 'DIMEXP_date_moy', 'DIMEXP_date_date', 'DIMEXP_date_month_seq'),
}
FACT_LIST = ','.join("N'" + name + "'" for name in FACTS)
PREFLIGHT = f"""
SET NOCOUNT ON;
IF (SELECT COUNT(*) FROM sys.indexes i JOIN sys.tables t ON t.object_id=i.object_id
    WHERE t.schema_id=SCHEMA_ID(N'dbo') AND t.name IN ({FACT_LIST})
    AND i.type=5 AND i.is_disabled=0) <> 7
    THROW 51000, 'Source must already have seven active fact CCIs; no conversion is performed', 1;
IF (SELECT COUNT(*) FROM sys.indexes i JOIN sys.tables t ON t.object_id=i.object_id
    WHERE t.schema_id=SCHEMA_ID(N'dbo') AND t.name IN ({FACT_LIST})
    AND i.is_primary_key=1 AND i.type=2 AND i.is_disabled=0) <> 7
    THROW 51000, 'Source must retain seven nonclustered fact primary keys', 1;
IF EXISTS (SELECT 1 FROM sys.indexes i JOIN sys.objects t ON t.object_id=i.object_id
    WHERE t.is_ms_shipped=0 AND i.index_id>0 AND
    (i.is_disabled=1 OR i.is_hypothetical=1 OR
     (i.is_primary_key=0 AND i.is_unique_constraint=0 AND NOT
      (i.type=5 AND t.schema_id=SCHEMA_ID(N'dbo') AND t.name IN ({FACT_LIST})))))
    THROW 51000, 'Use the clean CCI source without extra tuning indexes or indexed views', 1;
IF (SELECT COUNT(*) FROM sys.tables WHERE schema_id=SCHEMA_ID(N'dbo') AND name IN
    (N'customer',N'customer_address',N'customer_demographics',N'date_dim')) <> 4
    THROW 51000, 'The four dimensions must exist', 1;
IF EXISTS (SELECT 1 FROM sys.tables t WHERE t.schema_id=SCHEMA_ID(N'dbo') AND t.name IN
    (N'customer',N'customer_address',N'customer_demographics',N'date_dim') AND NOT EXISTS
    (SELECT 1 FROM sys.indexes i WHERE i.object_id=t.object_id AND i.is_primary_key=1 AND i.type=1))
    THROW 51000, 'Dimensions must retain their original clustered primary keys', 1;
SELECT @@VERSION AS server_version;
SELECT name,compatibility_level FROM sys.databases WHERE database_id=DB_ID();
SELECT name,value_in_use FROM sys.configurations WHERE name IN
    (N'max degree of parallelism',N'max server memory (MB)',N'cost threshold for parallelism');
"""
PREFLIGHT += '\n'.join(
    f"IF NOT EXISTS(SELECT 1 FROM dbo.[{table}]) THROW 51000, 'Empty table: {table}', 1;"
    for table in FACTS + ('customer', 'customer_address', 'customer_demographics', 'date_dim'))
INVENTORY = """
SET NOCOUNT ON;
SELECT s.name,t.name,COALESCE(i.name,N'HEAP'),i.type_desc,
       COALESCE((SELECT SUM(p.reserved_page_count)*8 FROM sys.dm_db_partition_stats p
                 WHERE p.object_id=i.object_id AND p.index_id=i.index_id),0),
       COALESCE((SELECT STRING_AGG(CONVERT(nvarchar(max),c.name+N':'+
          CONVERT(nvarchar(10),ic.key_ordinal)+N':'+CONVERT(nvarchar(1),ic.is_included_column)),N';')
          WITHIN GROUP(ORDER BY ic.index_column_id) FROM sys.index_columns ic
          JOIN sys.columns c ON c.object_id=ic.object_id AND c.column_id=ic.column_id
          WHERE ic.object_id=i.object_id AND ic.index_id=i.index_id),N'')
FROM sys.indexes i JOIN sys.tables t ON t.object_id=i.object_id
JOIN sys.schemas s ON s.schema_id=t.schema_id WHERE t.is_ms_shipped=0
ORDER BY s.name,t.name,i.index_id;
"""
FULLSCAN = """
SET NOCOUNT ON;
DECLARE @sql nvarchar(max);
SELECT @sql=STRING_AGG(CONVERT(nvarchar(max),N'UPDATE STATISTICS '+QUOTENAME(s.name)+N'.'+
    QUOTENAME(t.name)+N' WITH FULLSCAN;'),CHAR(10))
FROM sys.tables t JOIN sys.schemas s ON s.schema_id=t.schema_id WHERE t.is_ms_shipped=0;
EXEC sys.sp_executesql @sql;
ALTER DATABASE SCOPED CONFIGURATION CLEAR PROCEDURE_CACHE;
"""


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_csv(path, fields, rows):
    with Path(path).open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


class Sql:
    def __init__(self, container, timeout):
        self.container, self.timeout = container, timeout

    def execute(self, database, text, *, tabular=False, timeout=None):
        # The password stays inside the container and is never logged or passed on the host CLI.
        command = ['docker', 'exec', '-i', self.container, 'bash', '-c',
                   'export SQLCMDPASSWORD="$MSSQL_SA_PASSWORD"; '
                   'exec /opt/mssql-tools18/bin/sqlcmd "$@"', '--',
                   '-S', 'localhost', '-U', 'sa', '-C', '-b', '-r', '1', '-I',
                   '-d', database, '-t', str(timeout or self.timeout), '-w', '65535', '-i', '/dev/stdin']
        command += ['-h', '-1', '-W', '-s', '|'] if tabular else ['-y', '0']
        result = subprocess.run(command, input=(text + '\n').encode('utf-8'), capture_output=True,
                                timeout=(timeout or self.timeout) + 30)
        if result.returncode:
            raise RuntimeError(result.stdout.decode('utf-8', errors='replace') +
                               result.stderr.decode('utf-8', errors='replace'))
        return result.stdout


def inventory(sql, database, target):
    rows = []
    for line in sql.execute(database, INVENTORY, tabular=True).decode('utf-8').splitlines():
        if not line.strip():
            continue
        schema, table, name, kind, kb, columns = line.strip().split('|')
        rows.append(dict(schema=schema, table=table, index=name, type=kind,
                         reserved_kb=int(kb), columns=columns))
    write_csv(target, ['schema', 'table', 'index', 'type', 'reserved_kb', 'columns'], rows)
    return rows


def plan(sql, database, query, target):
    raw = sql.execute(database, 'SET SHOWPLAN_XML ON;\nGO\n' + query +
                      '\nGO\nSET SHOWPLAN_XML OFF;\nGO\n')
    target.write_bytes(raw)
    objects = set()
    for xml in re.findall(r'<ShowPlanXML\b.*?</ShowPlanXML>', raw.decode('utf-8'), re.S):
        root = ET.fromstring(xml)
        for obj in root.iter('{http://schemas.microsoft.com/sqlserver/2004/07/showplan}Object'):
            name = obj.get('Index', '').strip('[]')
            if name.startswith('DIMEXP_'):
                objects.add((obj.get('Table', '').strip('[]'), name))
    if b'<ShowPlanXML' not in raw:
        raise ValueError('No estimated plan returned; inspect ' + str(target))
    return sorted(objects)


def load_queries(args):
    queries_dir = (args.queries or ROOT / 'data' / (args.query_scale or args.scale) / 'queryset').resolve()
    files = (queries_dir / 'queries.lst').read_text(encoding='utf-8-sig').splitlines()
    if not files or len(files) != len(set(files)):
        raise ValueError('queries.lst must contain unique SQL filenames')
    if any(not re.fullmatch(r'[A-Za-z0-9_-]+\.sql', f) for f in files):
        raise ValueError('Invalid filename in queries.lst')
    return queries_dir, {f: (queries_dir / f).read_bytes() for f in files}


def run(args, inputs=None):
    queries_dir, queries = inputs if inputs is not None else load_queries(args)
    # Snapshot bytes, so edits during measurement cannot change an input query.
    order = list(SCENARIOS)
    random.Random(args.seed).shuffle(order)
    print(f'Source={args.database}; scale={args.scale}; query_scale={args.query_scale or args.scale}; '
          f'queries={len(queries)}; queryset={queries_dir}; order={order}', flush=True)
    if args.dry_run:
        print('Dry run: SQL inputs checked; no Docker/SQL connection or files created.')
        return
    sql = Sql(args.container, args.timeout)
    token = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '_' + uuid.uuid4().hex[:8]
    database = args.database + '_dimexp_' + token
    output = ROOT / 'benchmark_results' / 'dimension_indexes' / (args.scale + '_' + token)
    output.mkdir(parents=True)
    (output / 'STATUS').write_text('RUNNING\n')
    print('Results: ' + str(output), flush=True)
    try:
        (output / 'source_metadata.txt').write_bytes(sql.execute(args.database, PREFLIGHT))
        snapshot = output / 'source_queries'
        snapshot.mkdir()
        for name, data in queries.items():
            (snapshot / name).write_bytes(data)
        for name in ('run.py', 'indexes.sql', 'analyze.py'):
            (output / name).write_bytes((HERE / name).read_bytes())
        manifest = dict(protocol='dimension_indexes_v2', scale=args.scale,
                        query_scale=args.query_scale or args.scale, query_directory=str(queries_dir),
                        source_database=args.database, experiment_database=database,
                        queries={n: digest(d) for n, d in queries.items()}, scenarios=list(SCENARIOS),
                        scenario_order=order, seed=args.seed, passes=args.passes,
                        files={n: digest((HERE / n).read_bytes()) for n in ('run.py', 'indexes.sql', 'analyze.py')})
        (output / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        # Uses the source file directory, allowing named-volume installations without new mounts.
        clone = f"""
SET NOCOUNT ON;
IF DB_ID(N'{database}') IS NOT NULL THROW 51000, 'Experiment database already exists', 1;
IF EXISTS(SELECT 1 FROM sys.master_files WHERE database_id=DB_ID(N'{args.database}') AND type NOT IN(0,1))
    THROW 51000, 'Only ordinary data/log files are supported', 1;
DECLARE @dir nvarchar(4000), @backup nvarchar(4000), @moves nvarchar(max), @cmd nvarchar(max);
SELECT TOP(1) @dir=LEFT(physical_name,LEN(physical_name)-CHARINDEX('/',REVERSE(physical_name))+1)
FROM sys.master_files WHERE database_id=DB_ID(N'{args.database}') AND type=0 ORDER BY file_id;
SET @backup=@dir+N'{database}.bak';
BACKUP DATABASE [{args.database}] TO DISK=@backup WITH COPY_ONLY,CHECKSUM,COMPRESSION;
SELECT @moves=STRING_AGG(CONVERT(nvarchar(max),N'MOVE '+QUOTENAME(name,'''')+N' TO '+
    QUOTENAME(@dir+N'{database}_'+CONVERT(nvarchar(10),file_id)+
    CASE WHEN type=1 THEN N'.ldf' ELSE N'.mdf' END,'''')),N',')
FROM sys.master_files WHERE database_id=DB_ID(N'{args.database}');
SET @cmd=N'RESTORE DATABASE [{database}] FROM DISK='+QUOTENAME(@backup,'''')+N' WITH '+@moves+N',RECOVERY,CHECKSUM;';
EXEC sys.sp_executesql @cmd;
SELECT @backup AS retained_backup;
"""
        (output / 'clone.log').write_bytes(sql.execute('master', clone, timeout=7200))
        sql.execute(database, PREFLIGHT)
        (output / 'table_rows.txt').write_bytes(sql.execute(database, """SET NOCOUNT ON;
SELECT t.name,SUM(p.row_count) AS row_count FROM sys.tables t
JOIN sys.dm_db_partition_stats p ON p.object_id=t.object_id AND p.index_id IN(0,1)
GROUP BY t.name ORDER BY t.name;"""))
        initial = inventory(sql, database, output / 'initial_indexes.csv')
        unchanged = [r for r in initial if not r['index'].startswith('DIMEXP_')]
        setup, usage, checks = [], [], []
        ddl = (output / 'indexes.sql').read_text()
        for scenario in order:
            folder = output / scenario
            folder.mkdir()
            (folder / 'plans').mkdir()
            (folder / 'outputs').mkdir()
            print(f'[{scenario}] indexes, plans and FULLSCAN', flush=True)
            start = time.perf_counter()
            sql.execute(database, ddl.replace('$(EXPERIMENT_DB)', database).replace('$(SCENARIO)', scenario))
            setup_ms = (time.perf_counter() - start) * 1000
            # Create automatic stats during compilation, then normalize them before timing.
            for name, data in queries.items():
                plan(sql, database, data.decode('utf-8-sig'), folder / 'plans' / (name + '.xml'))
            (folder / 'statistics.log').write_bytes(sql.execute(database, FULLSCAN, timeout=7200))
            for name, data in queries.items():
                for table, index in plan(sql, database, data.decode('utf-8-sig'), folder / 'plans' / (name + '.xml')):
                    usage.append(dict(scenario=scenario, query_id=Path(name).stem, table=table, index=index))
            current = inventory(sql, database, folder / 'indexes.csv')
            if [r for r in current if not r['index'].startswith('DIMEXP_')] != unchanged:
                raise ValueError('An original index changed during the experiment')
            expected = set(sum(INDEXES.values(), ())) if scenario == 'all_dimensions' else set(INDEXES.get(scenario, ()))
            if {r['index'] for r in current if r['index'].startswith('DIMEXP_')} != expected:
                raise ValueError('Unexpected dimension index inventory')
            setup.append(dict(scenario=scenario, setup_ms=setup_ms,
                              added_kb=sum(r['reserved_kb'] for r in current if r['index'].startswith('DIMEXP_'))))
            timings = []
            for repeat in range(args.passes + 1):
                print(f'[{scenario}] ' + ('warm-up' if repeat == 0 else f'pass {repeat}/{args.passes}'), flush=True)
                for name, data in queries.items():
                    start = time.perf_counter()
                    raw = sql.execute(database, data.decode('utf-8-sig'))
                    elapsed = (time.perf_counter() - start) * 1000
                    q = Path(name).stem
                    if repeat == 0:
                        (folder / 'outputs' / (q + '.txt')).write_bytes(raw)
                        checks.append(dict(scenario=scenario, query_id=q, sha256=digest(raw)))
                    else:
                        timings.append(dict(pass_number=repeat, query_id=q, duration_ms=elapsed))
                write_csv(folder / 'timings.csv', ['pass_number', 'query_id', 'duration_ms'], timings)
        write_csv(output / 'setup.csv', ['scenario', 'setup_ms', 'added_kb'], setup)
        write_csv(output / 'estimated_index_usage.csv', ['scenario', 'query_id', 'table', 'index'], usage)
        write_csv(output / 'output_hashes.csv', ['scenario', 'query_id', 'sha256'], checks)
        (output / 'STATUS').write_text('COMPLETE\n')
        from analyze import analyze
        analyze(output)
        print('Complete. Open results.ipynb. Private database and backup retained: ' + database, flush=True)
    except Exception as error:
        (output / 'STATUS').write_text('FAILED\n')
        (output / 'error.txt').write_text(str(error), encoding='utf-8')
        raise


def parse_runs(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--databases', nargs=3, metavar=('DB_1GB', 'DB_2GB', 'DB_5GB'),
                        help='Default full experiment: 1GB, 2GB and 5GB in this order, each with its own queries')
    source.add_argument('--database', help='Single clean CCI source; requires --scale')
    parser.add_argument('--scale', choices=['1GB', '2GB', '5GB', '10GB'], help='Single-scale mode only')
    parser.add_argument('--query-scale', choices=['1GB', '2GB', '5GB', '10GB'],
                        help='Single-scale override; default is the measured scale')
    parser.add_argument('--queries', type=Path, help='Single-scale override of the host queryset directory')
    parser.add_argument('--container', default='tpcds_sqlserver')
    parser.add_argument('--passes', type=int, default=5)
    parser.add_argument('--seed', type=int, default=20261106, help='Reproducible shuffle of scenario order')
    parser.add_argument('--timeout', type=int, default=3600)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    databases = args.databases or [args.database]
    if any(not re.fullmatch(r'[A-Za-z0-9_]{1,60}', db) for db in databases):
        parser.error('Database name must contain 1-60 letters, digits or underscores')
    if args.passes < 2 or args.timeout < 1:
        parser.error('Use at least two measured passes and a positive timeout')
    if args.databases:
        if args.scale or args.query_scale or args.queries:
            parser.error('--databases runs 1GB/2GB/5GB with their own queries; overrides require --database --scale')
        if len(set(databases)) != 3:
            parser.error('Use a different source database for each scale')
        selections = zip(DEFAULT_SCALES, databases)
    else:
        if not args.scale:
            parser.error('--database requires --scale; use --databases DB_1GB DB_2GB DB_5GB for the full experiment')
        selections = [(args.scale, args.database)]
    runs = []
    for scale, database in selections:
        selected = argparse.Namespace(**vars(args))
        selected.database, selected.scale = database, scale
        selected.query_scale = args.query_scale or scale
        runs.append(selected)
    return runs


def main():
    # Validate/snapshot every selected queryset before any database clone or SQL work.
    runs = parse_runs()
    inputs = [load_queries(args) for args in runs]
    for args, selected in zip(runs, inputs):
        run(args, selected)


if __name__ == '__main__':
    main()
