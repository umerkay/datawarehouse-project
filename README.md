# TPC-DS SQL Server Benchmark

This project provides a reproducible, two-container environment for running a TPC-DS benchmark against SQL Server. 

To guarantee perfect reproducibility across different host operating systems, the architecture is strictly isolated:
* **`tpcds_workspace` (Ubuntu):** Provides an isolated Linux build environment to compile the TPC-DS C-code and generate the raw data, preventing OS-level compilation issues (like macOS Apple Silicon constraints).
* **`tpcds_sqlserver` (SQL Server 2022):** Hosts the database engine, stores the data on a persistent Docker volume, and executes the data loading and benchmark scripts natively.

## Prerequisites
* Docker and Docker Compose installed.
* Manually optimized T-SQL queries placed in the `optimized_queries/` directory at the root of the project.

---

## Workflow

### 1. Spin up the Environment

Build and start both containers in the background. This will automatically mount the shared folders (`/tpcds-kit`, `/scripts`, `/optimized_queries`) and create a permanent storage volume for the database.

```bash
docker compose up -d

```

*(Note: Wait ~15 seconds for the SQL Server engine to fully initialize before proceeding.)*

### 2. Generate TPC-DS Data

Compile the TPC-DS tools and generate the flat `.dat` files natively inside the Ubuntu workspace container. By default, this script generates the 1GB, 2GB, 5GB, and 10GB scale factors. Optionally pass one or more positive integer scale factors to generate only those sizes; each is written to a `data_<size>GB` folder.

```bash
# Generate the default 1GB, 2GB, 5GB, and 10GB datasets
docker exec -it tpcds_workspace bash /scripts/01_generate_data.sh

# Generate only the 1GB and 2GB datasets
docker exec -it tpcds_workspace bash /scripts/01_generate_data.sh 1 2

```

### 3. Load Data into SQL Server

Execute the loading script inside the SQL Server container. This script drops any existing database with the same name, creates a fresh schema, and bulk inserts the data.

**Usage:** `bash /scripts/02_load_data.sh <SCALE_FOLDER> <DB_NAME>`

```bash
docker exec -it tpcds_sqlserver bash /scripts/02_load_data.sh 1GB tpcds_01

```

### 4. Run the Benchmark

Execute the benchmark script against the populated database. This script runs directly inside the SQL Server container.

**Protocol:** The script automatically finds all `.sql` files in the `/optimized_queries` folder. It runs the entire suite **6 times**. The first run is discarded to allow for buffer pool warm-up, and the remaining 5 runs are averaged. Execution times are tracked with millisecond precision.

**Usage:** `bash /scripts/03_run_benchmark.sh <DB_NAME>`

```bash
docker exec -it tpcds_sqlserver bash /scripts/03_run_benchmark.sh tpcds_01

```

---

## Resetting the Environment

* **Reset a specific database:** Simply re-run the `02_load_data.sh` script for that database. It is idempotent and will cleanly rebuild it.
* **Total Factory Reset:** To wipe all databases completely and delete the persistent SQL Server volume, spin down the stack using the `-v` flag:
```bash
docker compose down -v

```