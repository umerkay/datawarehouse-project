# Add plans for 5GB or 10GB

You do not need SQL Server, Docker or the raw datasets to view/build the site.
They are needed only when capturing new plans. The analyzer never loads a
database or changes indexes, statistics, recovery models or server settings.

1. Use a fully populated database for the intended size. Check its data,
   constraints, indexes and server settings. The included 5GB capture was made
   after completing and verifying the earlier partial load; verify any database
   used for a new capture independently.
2. Export estimated plans from SQL Server/SSMS, or use the read-only capture
   helper below with trusted generated SELECT queries. Estimated plans do not
   provide measured duration or runtime rows. Export actual plans only when you
   intend to execute the query.
3. Add files under `tools/query_analyzer/datasets/5GB/plans/` or `10GB/plans/`. Preserve
   queryset filename stems: `query_001.xml` or `query_001.sqlplan`, etc. A complete
   current queryset has 103 entries. Partial contributions are allowed; the site
   reports actual coverage and missing queries without inventing plans.
4. Optionally replace that size's `timings.csv` with a profiler summary containing
   `query_id,average_ms`. Use finite nonnegative numbers and unique query IDs.
   Measurements should use the same SQL, dataset, server settings and the project's
   one-warm-up/five-measured-run protocol. State changes to these conditions in
   your pull request.
5. Package the capture into a single archive, then test and rebuild from the repository root:

   ```bash
   python -B -m tools.query_analyzer pack 5GB
   python -B -m unittest discover -s tools/query_analyzer/tests -v
   python -B -m tools.query_analyzer build
   ```

6. Check the coverage cards, open several plan trees and review the pending states
   for missing inputs. Commit `datasets/5GB/plans.zip` (or `10GB/plans.zip`), CSVs and source-code changes;
   generated `site/` files are excluded from Git. Open a pull request describing
   database/version, size, index changes, estimated/actual mode and capture date.

Plan XML contains the SQL text and may contain literals. Review new captures for
private data before committing; the included TPC-DS inputs are synthetic.

The ZIP contains the unchanged original plan files. The site reads it directly
without extracting files. Loose capture directories are local and ignored by Git;
this keeps each dataset contribution to one archive instead of 103 new files.
Use `pack 10GB` for the other size, or `pack 5GB --input /path/to/exported/plans`
for an external capture folder. Archive members must be flat plan filenames, at
most 32 MiB each. Unpack locally when reviewing individual XML changes; GitHub
does not display per-query diffs inside a binary ZIP.

## Capture using the existing project containers

After the target database is already loaded and split queries exist:

```bash
docker compose exec sqlserver bash /tools/query_analyzer/scripts/capture_plans.sh 5GB tpcds_5
docker compose exec sqlserver bash /tools/query_analyzer/scripts/capture_plans.sh 10GB tpcds_10
```

The default output goes directly into the corresponding `datasets/<size>/plans/`
directory through the project's mount. Recreate the Compose containers if the
`/tools/query_analyzer` mount has not been added; do not reset database volumes.

For a local SQL Server/sqlcmd installation, configure `QUERY_DIR`, `SQLCMD_BIN`,
`SQL_SERVER`, `SQL_USER`, and `SQLCMDPASSWORD` in your environment. The helper also
accepts the project's `MSSQL_SA_PASSWORD` variable. It does not read or publish
`.env`, and never passes the password as a command-line argument. Use an account
with SELECT and SHOWPLAN permissions on the referenced databases.

```bash
QUERY_DIR=/path/to/queryset SQLCMD_BIN=/path/to/sqlcmd \
  bash tools/query_analyzer/scripts/capture_plans.sh 5GB tpcds_5 1 3 15
```

`OUTPUT_DIR` overrides the destination; `PLAN_TIMEOUT` defaults to 120 seconds.
`SQLCMDPASSWORD` must already be set. The helper uses Bash and the ODBC sqlcmd
flags used by this project; it validates listed filenames/indices first. It
does not run the benchmark or generate timings. Capture failures are saved as
ignored `*.failed.txt` files; the build validates the resulting XML separately.
