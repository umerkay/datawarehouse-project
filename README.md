# TPC-DS SQL Server Benchmark

This project provides a reproducible, two-container environment for running a TPC-DS benchmark against SQL Server.

To guarantee perfect reproducibility across different host operating systems, the architecture is strictly isolated:
* **`tpcds_workspace` (Ubuntu):** Provides an isolated Linux build environment to compile the TPC-DS C-code and generate the raw data, preventing OS-level compilation issues (like macOS Apple Silicon constraints).
* **`tpcds_sqlserver` (SQL Server 2022):** Hosts the database engine, stores the data on a persistent Docker volume, and executes the data loading and benchmark scripts natively.

## Prerequisites
* Docker and Docker Compose installed.

---

## Workflow

### 1. Spin up the Environment

Build and start both containers in the background. The workspace build installs all required compilation and Python dependencies before its container starts. Compose will also mount the shared folders (`/tpcds-kit`, `/scripts`, `/data`) and create a permanent storage volume for the database.

```bash
docker compose up -d --build

```

*(Note: Wait ~15 seconds for the SQL Server engine to fully initialize before proceeding.)*

### 2. Generate TPC-DS Data

Compile the TPC-DS tools and generate the flat `.dat` files natively inside the Ubuntu workspace container. By default, this script generates the 1GB, 2GB, 5GB, and 10GB scale factors. It uses the number of available CPU cores and launches one `dsdgen` process per core for each scale factor, then concatenates each table's partitions into one `tablename.dat` file. Optionally pass one or more positive integer scale factors to generate only those sizes; each is written to a `/data/<size>GB/dataset` folder.

```bash
# Generate the default 1GB, 2GB, 5GB, and 10GB datasets
docker exec -it tpcds_workspace bash /scripts/01_generate_data.sh

# Generate only the 1GB and 2GB datasets
docker exec -it tpcds_workspace bash /scripts/01_generate_data.sh 1 2

# Generate the default datasets without parallelism
docker exec -it tpcds_workspace bash /scripts/01_generate_data.sh --no-parallel

# For development: after optimizing a query, you can regenerate just the query sets (without recompiling dsdgen, and splitting queries into separate files) with:
docker exec -it tpcds_workspace bash /scripts/01_generate_data.sh --queryset --split-queries --no-recompile

```

### 3. Load Data into SQL Server

Execute the loading script inside the SQL Server container. This script drops any existing database with the same name, creates a fresh schema, and bulk inserts the data.

**Usage:** `bash /scripts/02_load_data.sh <SCALE_FOLDER> <DB_NAME> [--full-stats] [--optimized]`

`--full-stats` updates statistics for all TPC-DS workload tables with `FULLSCAN` after bulk loading and referential integrity setup, then clears the database-scoped procedure cache using separate maintenance scripts. It is intentionally opt-in and is not included in the script's default behavior.

```bash
docker exec -it tpcds_sqlserver bash /scripts/02_load_data.sh 1GB tpcds_01 --full-stats

# Load from the optimized schema setup scripts with
docker exec -it tpcds_sqlserver bash /scripts/02_load_data.sh 1GB tpcds_01_optimized --optimized --full-stats

```

### 4. Apply the analytical indexing strategy

For an optimized comparison, load the same scale factor into a separate database and apply the reproducible strategy in `scripts/indexing_strategy.sql`. The strategy keeps the schema's clustered primary keys, creates covering nonclustered columnstore indexes on all seven fact tables, and adds a small set of selective rowstore indexes for dimension filters and return anti-joins. Fact statistics use a 10% sample while small dimensions use `FULLSCAN`, keeping preparation cost practical as scale grows. Index creation and statistics updates happen before benchmarking and are not included in query timings.

```bash
docker exec -it tpcds_sqlserver \
  bash /scripts/02_load_data.sh 1GB tpcds_01_indexed --full-stats

docker exec -it tpcds_sqlserver \
  bash /scripts/05_apply_indexing.sh tpcds_01_indexed
```

The strategy is idempotent and can be reapplied safely. Keep an unindexed database, such as `tpcds_01`, for baseline comparisons.

#### Measured engineering result (2026-10-26)

On the local SQL Server 2022 Docker environment, one complete measured pass of the 103 generated queries produced:

| Database | Scale | Full-suite elapsed time | Relative result |
| --- | ---: | ---: | ---: |
| `tpcds_sf1_baseline` | 1GB | 633.975 s | baseline |
| `tpcds_sf1_optimized` | 1GB | 65.636 s | 9.66x faster; 89.6% less time |
| `tpcds_sf2_optimized` | 2GB | 89.185 s | 1.36x the optimized 1GB time |

These are internal, single-pass engineering measurements, not official or publication-comparable TPC benchmark results. Use the default five measured repetitions below for final reporting. The optimized 1GB database allocated 2,448 MB versus 1,744 MB for the baseline: about 704 MB (40.4%) additional space in this run.

### 5. Run the Benchmark

Execute the benchmark script against the populated database. This script runs directly inside the SQL Server container.

**Protocol:** The script requires and reads only `/data/<SCALE_FOLDER>/queryset/benchmark.sql`. By default it runs this benchmark **6 times**. The first run is discarded to allow for buffer pool warm-up, and the remaining 5 runs are averaged. Execution times are tracked with millisecond precision. For short tuning experiments, override `WARMUP_RUNS` and `MEASURED_RUNS`; defaults remain 1 and 5.

**Usage:** `bash /scripts/03_run_benchmark.sh <SCALE_FOLDER> <DB_NAME> [--full-stats-after-warmup]`

Use `--full-stats-after-warmup` to run [`scripts/full_stats.sql`](scripts/full_stats.sql) after the discarded warm-up and before the five measured runs. It updates statistics but does not clear the procedure cache. The statistics-maintenance time is not included in the measured query timings. Without the flag, benchmark behavior is unchanged.

```bash
docker exec -it tpcds_sqlserver \
  bash /scripts/03_run_benchmark.sh 1GB tpcds_01 --full-stats-after-warmup

# One-pass engineering experiment (not the final reporting protocol)
docker exec -e WARMUP_RUNS=0 -e MEASURED_RUNS=1 \
  tpcds_sqlserver bash /scripts/03_run_benchmark.sh 1GB tpcds_01

```

### 6. Development: Profile Individual Queries

Use the separate profiling script when you want diagnostic timings for each query rather than the normal aggregate benchmark. It reads one SQL filename per row from `data/<SCALE_FOLDER>/queryset/queries.lst` and executes those pre-split files directly, ignoring unlisted SQL files. By default, it runs each selected query once for warm-up and five measured times, then ranks the queries by average duration. Set the environment variables `WARMUP_RUNS` (integer >= 0) and `MEASURED_RUNS` (integer >= 1) to override these counts. Warm-up timings are discarded, and measured runs in `timings.csv` are numbered starting at 1. It logs each query before and after execution; on failure, it reports the query, run, elapsed time, and `sqlcmd` exit code. Add `--full-stats-after-warmup` to update statistics between the warm-up pass and measured passes without clearing the procedure cache.

If `queries.lst` is missing, the script prints a warning and exits without profiling. Generate the split queries and list with:

```bash
docker exec -it tpcds_workspace bash /scripts/01_generate_data.sh --queryset --split-queries --no-recompile
```

**Usage:** `bash /scripts/04_profile_queries.sh <SCALE_FOLDER> <DB_NAME> [--from INDEX] [--to INDEX] [--list INDEX_1 INDEX_2 ...] [--optimized] [--full-stats-after-warmup] [--compare-to PATH] [--out PATH]`

Query indices start at 1, and both range endpoints are inclusive. `--list` overrides the range. If a query fails, profiling stops immediately: its failed execution is not added to `timings.csv`, and no final summary or HTML report is generated for that incomplete run.

```bash
docker exec -it tpcds_sqlserver bash /scripts/04_profile_queries.sh 1GB tpcds_01

# Profile the optimized version of the queries
docker exec -it tpcds_sqlserver bash /scripts/04_profile_queries.sh 1GB tpcds_01 --optimized

# Profile only the first and third entries in queries.lst
docker exec -it tpcds_sqlserver bash /scripts/04_profile_queries.sh 1GB tpcds_01 --list 1 3

# Profile those queries with two warm-up runs and ten measured runs
docker exec -it -e WARMUP_RUNS=2 -e MEASURED_RUNS=10 tpcds_sqlserver bash /scripts/04_profile_queries.sh 1GB tpcds_01 --list 1 3

# Also compare the new run against a previous run (path inside the container)
docker exec -it tpcds_sqlserver bash /scripts/04_profile_queries.sh 1GB tpcds_01 \
  --compare-to /benchmark_results/<prev_run_dir>

# Write directly to a named directory instead of a timestamped one
docker exec -it tpcds_sqlserver bash /scripts/04_profile_queries.sh 1GB tpcds_01 \
  --out /benchmark_results/my_profile

```

**SQL Server compatibility notes:**

* Q23 (generated from `query22.tpl`) casts `inv_quantity_on_hand` to `decimal(19,4)` before `AVG`; without this cast, SQL Server reports an integer overflow for the 5GB and 10GB datasets.
* The channel-comparison query generated from `query78.tpl` pushes its final year into each channel CTE and uses a hash-join hint. This prevents a severe nested-loop cardinality regression observed at 2GB (about 6.56 million logical reads versus about 9 thousand at 1GB before the fix).
* The AM/PM ratio generated from `query90.tpl` uses `NULLIF` for the denominator. Non-official small scale factors can legitimately produce zero matching PM rows; the original expression aborted the whole suite with divide-by-zero.

By default, each profiling run creates a timestamped directory under
`benchmark_results/` (or `$RESULTS_DIR`). Use `--out PATH` to write directly to
that directory instead, without appending a timestamp or database name.
Relative paths are resolved from the script's working directory; inside Docker,
use a container path such as `/benchmark_results/my_profile`. Missing parent
directories are created. Existing output files are overwritten, so use a separate
directory from the `--compare-to` baseline.

The output directory contains:

* `timings.csv`: every measured execution time for each query.
* `summary.csv`: queries ranked from slowest to fastest by their average execution time.
* `report.html`: a self-contained horizontal bar chart of the ranked averages. Open this file in a browser to identify the slowest queries; the top 10 are highlighted in red.

With `--compare-to`, the previous run directory (or its `summary.csv`) is used as
the baseline, and the new run also contains `cmp_summary.csv` and `cmp_report.html`.
Comparison requires Python 3 in the environment running the profiling script;
without this flag, profiling has no Python dependency.

To compare existing runs directly on the host (Python 3, standard library only):

```bash
./scripts/cmp_query_profiles benchmark_results/NEW/summary.csv \
  benchmark_results/BASELINE/summary.csv --report cmp_report.html > cmp_summary.csv
```

The [comparison script](scripts/cmp_query_profiles) accepts two CSV files or run
directories. It prints CSV to stdout and writes `report.html` in the current
directory unless `--report PATH` is given. A is the current run, B the baseline:
rows are joined by `query_id` and ordered by A's rank. All original A columns are
preserved, followed by `_delta` and `_delta_pct` columns for `average_ms`, `min_ms`,
`max_ms`, `total_ms`, and `percent_of_total`. Deltas are **A - B**, with percentages
calculated as **100 * (A - B) / B**. For example, 80 ms versus 100 ms is -20 ms
(-20%), shown in smaller green text; increases are red and unchanged values gray.
Share-of-total deltas are percentage points, not a measure of query speedup.

Queries missing from B remain in the output with empty delta fields (`N/A` in
HTML); queries only in B are omitted. A zero baseline yields an undefined
percentage (empty CSV / `N/A`), except zero versus zero is 0%. Compare averages,
not totals, when the measured-run counts differ.

Query names are the listed SQL filenames without the `.sql` extension, for example `query_003` for `data/<SCALE_FOLDER>/queryset/query_003.sql`. Individual timings include the small, approximately constant `sqlcmd` process startup overhead. This profiling script is intended for optimization work; continue to use `03_run_benchmark.sh` for comparable full-suite benchmark results.

### Validate optimized query results

Run [check_query_equivalence](scripts/check_query_equivalence) on the host:

```bash
./scripts/check_query_equivalence 5GB tpcds_05 tpcds_05_optimized 71 6 23 24
```

It uses `docker exec` to run inside `tpcds_sqlserver` (override with
`--container NAME`). IDs such as `71`, `query071`, and `query_071` all select
`query_071.sql`. For each ID, it executes `/data/<scale>/queryset/` against the
first database and `/data/<scale>/queryset-optimized/` against the second.
Two queries run concurrently, one original/optimized pair at a time.

The container needs Python 3, `pyodbc`, ODBC Driver 18 for SQL Server, and its
existing `MSSQL_SA_PASSWORD` environment variable. If `pyodbc` is missing, install
it once (repeat after recreating the SQL Server container):

```bash
docker exec -u 0 tpcds_sqlserver sh -c \
  'apt-get update && apt-get install -y python3-pyodbc'
```

The checker streams typed values without `sqlcmd` text formatting or truncation.
It compares every result set, column count, row count, and cell value, stopping
at the first mismatch per query and continuing to the next pair. Column aliases
and SQL type metadata are not compared. Numeric values use exact equality (no
tolerance); `NULL` differs from an empty string. Row order is significant:
unordered queries or non-unique `ORDER BY` ties can fail despite containing the
same rows. Ensure deterministic ordering and unchanged data in both databases.
Only run trusted, read-only query files: the checker executes SQL as-is, as `sa`.

The default query timeout is 300 seconds; override with `--timeout SECONDS`.
Exit status is **0** when all pairs match, **1** for differing results, and **2**
for execution/setup errors (Docker itself may return another nonzero status).

### 7. Development: Visualize Join Relationships Without Running Queries

The separate [static join-graph tool](tools/join_graph/README.md) analyzes only the SQL files listed in `data/1GB/queryset/queries.lst`, using the bundled schema to resolve column names. It does not connect to SQL Server or change indexes.

All generated graphs, XML plans, CSV/JSON reports and diagnostic dumps belong under the **Git-ignored** `tools/join_graph/generated/` directory; only tool source, tests, samples and documentation should be committed. Open the generated [interactive graph](tools/join_graph/generated/static/join_graph.html) in a browser, or review the [static SVG](tools/join_graph/generated/static/join_graph.svg). Edge details show joined attributes, distinct-query counts, total predicate occurrences and supporting query IDs. CSV/JSON exports and a [coverage and next-steps summary](tools/join_graph/generated/static/README.md) make the results auditable; unresolved CTE/derived relationships are flagged rather than guessed.

To regenerate from the repository root (Python 3.10+):

```bash
python3 -m venv tools/join_graph/.venv
source tools/join_graph/.venv/bin/activate
python -m pip install -r tools/join_graph/requirements.txt
python tools/join_graph/analyze_workload.py --fail-on-parse-errors
```

Use the graph to shortlist index experiments, then validate those experiments with execution plans and the existing baseline/indexed profiling and benchmark workflow. Join frequency alone does not establish index benefit.

Estimated-plan companions are available for the [baseline database](tools/join_graph/generated/plans-baseline/join_graph.html) and [indexed database](tools/join_graph/generated/plans-indexed/join_graph.html). They compile without executing the workload. Actual-plan graphs for [baseline](tools/join_graph/generated/actual-baseline/join_graph.html) and [indexed](tools/join_graph/generated/actual-indexed/join_graph.html) add measured elapsed/CPU time and estimated-versus-actual join row deviations, with explicit timeouts. **Actual collection executes SELECTs once with deadlines**; it is separate from the repeated benchmark protocol. SQL Server optimizer cost is not milliseconds, so no misleading cost-to-time percentage is calculated. See the [tool instructions](tools/join_graph/README.md) for regeneration commands, metric definitions and limitations.

Each graph also analyzes **how** tables are used. Clicking a table shows its most filtered columns (index candidates), aggregated measures (columnstore candidates), GROUP BY granularity (for example `date_dim` grouped by day, month or year, which helps decide on materialized summaries) and existing indexes. Clicking a join shows the aggregation levels of the queries that use it. The **Where to start optimizing** section at the bottom of each page lists rule-based hints with supporting queries and a searchable column-usage table. These hints are hypotheses to test with `05_apply_indexing.sh` and the benchmark, not automatic recommendations.

---

## Analyze query execution plans

The shareable offline analyzer is in [`query_analyzer/`](query_analyzer/README.md).
It includes captured plans for 1GB, 2GB and 5GB, historical timing summaries, and
instructions for additional captures. The 10GB plans are pending. No raw datasets
or SQL connection are needed for the website.

```bash
python -B -m query_analyzer build
python -m http.server 8000 --directory query_analyzer/site
```

Open **http://localhost:8000/** or `query_analyzer/site/index.html`.
See the [contribution guide](query_analyzer/CONTRIBUTING.md) to add plans and the
[tool README](query_analyzer/README.md) for tests and optional GitHub Pages publishing.

## Resetting the Environment

* **Reset a specific database:** Simply re-run the `02_load_data.sh` script for that database. It is idempotent and will cleanly rebuild it.
* **Total Factory Reset:** To wipe all databases completely and delete the persistent SQL Server volume, spin down the stack using the `-v` flag:
```bash
docker compose down -v

```
