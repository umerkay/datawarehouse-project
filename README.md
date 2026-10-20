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

```

### 4. Run the Benchmark

Execute the benchmark script against the populated database. This script runs directly inside the SQL Server container.

**Protocol:** The script requires and reads only `/data/<SCALE_FOLDER>/queryset/benchmark.sql`. It runs this benchmark **6 times**. The first run is discarded to allow for buffer pool warm-up, and the remaining 5 runs are averaged. Execution times are tracked with millisecond precision.

**Usage:** `bash /scripts/03_run_benchmark.sh <SCALE_FOLDER> <DB_NAME>`

```bash
docker exec -it tpcds_sqlserver bash /scripts/03_run_benchmark.sh 1GB tpcds_01

```

### 5. Development: Profile Individual Queries

Use the separate profiling script when you want diagnostic timings for each query rather than the normal aggregate benchmark. It reads one SQL filename per row from `data/<SCALE_FOLDER>/queryset/queries.lst` and executes those pre-split files directly, ignoring unlisted SQL files. It runs each selected query once for warm-up and five measured times, then ranks the queries by average duration. It logs each query before and after execution; on failure, it reports the query, run, elapsed time, and `sqlcmd` exit code.

If `queries.lst` is missing, the script prints a warning and exits without profiling. Generate the split queries and list with:

```bash
docker exec -it tpcds_workspace bash /scripts/01_generate_data.sh --queryset --split-queries --no-recompile
```

**Usage:** `bash /scripts/04_profile_queries.sh <SCALE_FOLDER> <DB_NAME> [--from INDEX] [--to INDEX] [--list INDEX_1 INDEX_2 ...]`

```bash
docker exec -it tpcds_sqlserver bash /scripts/04_profile_queries.sh 1GB tpcds_01

# Profile only the first and third entries in queries.lst
docker exec -it tpcds_sqlserver bash /scripts/04_profile_queries.sh 1GB tpcds_01 --list 1 3

```

**SQL Server compatibility note:** Q23 (generated from `query22.tpl`) casts `inv_quantity_on_hand` to `decimal(19,4)` before `AVG`; without this cast, SQL Server reports an integer overflow for the 5GB and 10GB datasets.

Each profiling run creates a timestamped directory under `benchmark_results/` containing:

* `timings.csv`: every measured execution time for each query.
* `summary.csv`: queries ranked from slowest to fastest by their average execution time.
* `report.html`: a self-contained horizontal bar chart of the ranked averages. Open this file in a browser to identify the slowest queries; the top 10 are highlighted in red.

Query names are the listed SQL filenames without the `.sql` extension, for example `query_003` for `data/<SCALE_FOLDER>/queryset/query_003.sql`. Individual timings include the small, approximately constant `sqlcmd` process startup overhead. This profiling script is intended for optimization work; continue to use `03_run_benchmark.sh` for comparable full-suite benchmark results.

---

## Resetting the Environment

* **Reset a specific database:** Simply re-run the `02_load_data.sh` script for that database. It is idempotent and will cleanly rebuild it.
* **Total Factory Reset:** To wipe all databases completely and delete the persistent SQL Server volume, spin down the stack using the `-v` flag:
```bash
docker compose down -v

```