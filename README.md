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

**Usage:** `bash /scripts/02_load_data.sh <SCALE_FOLDER> <DB_NAME>`

```bash
docker exec -it tpcds_sqlserver bash /scripts/02_load_data.sh 1GB tpcds_01

# Load from the optimized schema setup scripts with
docker exec -it tpcds_sqlserver bash /scripts/02_load_data.sh 1GB tpcds_01_optimized --optimized

```

### 4. Apply the analytical indexing strategy

For an optimized comparison, load the same scale factor into a separate database and apply the reproducible strategy in `scripts/indexing_strategy.sql`. The strategy uses nonclustered columnstore indexes because the schema already creates clustered primary-key indexes on the fact tables. Index creation and statistics updates happen before benchmarking and are not included in query timings.

```bash
docker exec -it tpcds_sqlserver \
  bash /scripts/02_load_data.sh 1GB tpcds_01_indexed

docker exec -it tpcds_sqlserver \
  bash /scripts/05_apply_indexing.sh tpcds_01_indexed
```

The strategy is idempotent and can be reapplied safely. Keep an unindexed database, such as `tpcds_01`, for baseline comparisons.

### 5. Run the Benchmark

Execute the benchmark script against the populated database. This script runs directly inside the SQL Server container.

**Protocol:** The script requires and reads only `/data/<SCALE_FOLDER>/queryset/benchmark.sql`. It runs this benchmark **6 times**. The first run is discarded to allow for buffer pool warm-up, and the remaining 5 runs are averaged. Execution times are tracked with millisecond precision.

**Usage:** `bash /scripts/03_run_benchmark.sh <SCALE_FOLDER> <DB_NAME>`

```bash
docker exec -it tpcds_sqlserver bash /scripts/03_run_benchmark.sh 1GB tpcds_01

```

### 6. Development: Profile Individual Queries

Use the separate profiling script when you want diagnostic timings for each query rather than the normal aggregate benchmark. It reads one SQL filename per row from `data/<SCALE_FOLDER>/queryset/queries.lst` and executes those pre-split files directly, ignoring unlisted SQL files. By default, it runs each selected query once for warm-up and five measured times, then ranks the queries by average duration. Set the environment variables `WARMUP_RUNS` (integer >= 0) and `MEASURED_RUNS` (integer >= 1) to override these counts. Warm-up timings are discarded, and measured runs in `timings.csv` are numbered starting at 1. It logs each query before and after execution; on failure, it reports the query, run, elapsed time, and `sqlcmd` exit code.

If `queries.lst` is missing, the script prints a warning and exits without profiling. Generate the split queries and list with:

```bash
docker exec -it tpcds_workspace bash /scripts/01_generate_data.sh --queryset --split-queries --no-recompile
```

**Usage:** `bash /scripts/04_profile_queries.sh <SCALE_FOLDER> <DB_NAME> [--from INDEX] [--to INDEX] [--list INDEX_1 INDEX_2 ...]`

Query indices start at 1, and both range endpoints are inclusive. `--list` overrides the range. If a query fails, profiling stops immediately: its failed execution is not added to `timings.csv`, and no final summary or HTML report is generated for that incomplete run.

```bash
docker exec -it tpcds_sqlserver bash /scripts/04_profile_queries.sh 1GB tpcds_01

# Profile the optimized version of the queries
docker exec -it tpcds_sqlserver bash /scripts/04_profile_queries.sh 1GB tpcds_01 --optimized

# Profile only the first and third entries in queries.lst
docker exec -it tpcds_sqlserver bash /scripts/04_profile_queries.sh 1GB tpcds_01 --list 1 3

# Profile those queries with two warm-up runs and ten measured runs
docker exec -it -e WARMUP_RUNS=2 -e MEASURED_RUNS=10 tpcds_sqlserver bash /scripts/04_profile_queries.sh 1GB tpcds_01 --list 1 3

```

**SQL Server compatibility note:** Q23 (generated from `query22.tpl`) casts `inv_quantity_on_hand` to `decimal(19,4)` before `AVG`; without this cast, SQL Server reports an integer overflow for the 5GB and 10GB datasets.

Each profiling run creates a timestamped directory under `benchmark_results/` containing:

* `timings.csv`: every measured execution time for each query.
* `summary.csv`: queries ranked from slowest to fastest by their average execution time.
* `report.html`: a self-contained horizontal bar chart of the ranked averages. Open this file in a browser to identify the slowest queries; the top 10 are highlighted in red.

Query names are the listed SQL filenames without the `.sql` extension, for example `query_003` for `data/<SCALE_FOLDER>/queryset/query_003.sql`. Individual timings include the small, approximately constant `sqlcmd` process startup overhead. This profiling script is intended for optimization work; continue to use `03_run_benchmark.sh` for comparable full-suite benchmark results.

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
