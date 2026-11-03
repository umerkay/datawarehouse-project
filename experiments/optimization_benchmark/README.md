# Sales index experiment

Does adding B-tree indexes improve sales queries after adopting clustered
columnstore (CCI)? All scenarios retain the same seven fact CCIs, nonclustered
primary keys and original dimension constraints.

| Scenario | Additional indexes |
|---|---|
| `baseline` | None: CCI reference. |
| `date_keys` | Nonclustered indexes on `ss_sold_date_sk`, `cs_sold_date_sk` and `ws_sold_date_sk`. |
| `date_shared` | Same date indexes; store_sales adds INCLUDE `(ss_item_sk, ss_customer_sk, ss_quantity, ss_sales_price)`. |

Only the store_sales index changes between the last two scenarios. No inventory
index is added. Run all three configurations at **1GB, 2GB and 5GB**, matching
the columnstore study.

## Run

From the project root, use Bash or Git Bash, Docker Compose and Python 3.12+.
The existing SQL Server service, `.env`, loaded database and split query files
are required. The first run installs plotting dependencies in a local `.venv`.

Use the databases from the columnstore study, created with
[`tpcds-optimized/tools/tpcds.sql`](../../tpcds-optimized/tools/tpcds.sql).
This is the schema from main at commit `558fe97`, including the inventory CCI
added in `8cbc1b8`. It defines seven `IMCCI_*` indexes and nonclustered fact PKs.
The experiment requires this configuration without additional tuning indexes.
It backs up and restores a private copy; it does not create or convert CCIs.
Allow space for the retained backup, database copy and additional indexes.

If you need a new source database, the existing loader now supports `--optimized`:

```sh
# Creates/replaces the named database: use a new name to preserve existing data.
docker compose exec -T sqlserver bash /scripts/02_load_data.sh 1GB tpcds_01_cci --optimized
```

The Compose SQL Server service mounts `tpcds-optimized`; recreate the service
if your running container predates this mount. No reload is needed when the
study's CCI databases already exist.
Replace the three example names below with your actual databases, in
**1GB, 2GB, 5GB order**. Scales run sequentially and stop if a run fails.

```sh
# Check selection and container access without running SQL.
bash experiments/optimization_benchmark/run.sh --databases tpcds_01 tpcds_02 tpcds_05 --dry-run

# Run the complete experiment: three scenarios at each of three scales.
bash experiments/optimization_benchmark/run.sh --databases tpcds_01 tpcds_02 tpcds_05
```

The dry run does not validate SQL credentials, schema or free disk space.
Run one benchmark at a time and avoid Docker restarts during measurement.
For a single scale, use `--scale 2GB --database tpcds_02` instead of `--databases`.
All sizes use the
1GB queryset by default; `--query-scale` overrides it. Keep SQL identical across
compared runs.

## Measurements and results

Each scenario runs all 103 queries with one excluded warm-up and five measured
passes: **1,545 measurements per scale**, or **4,635 across 1GB, 2GB and 5GB**.
Statistics use FULLSCAN before warm-up.
Index creation and plan capture are outside query timing. CCIs are inherited
unchanged from the source. The schema SQL and its hash are saved with the run.
Times include the `sqlcmd` process and output transfer. Scenario order is fixed;
data buffers are not cleared.

Results are saved under `benchmark_results/optimization_<id>/findings_<scale>_<id>/`.
Each scale has a separate results folder. Compare relative improvements over
its own CCI baseline, variability and additional index space at each scale.
Start with these files in its `analysis/` folder:

- `RESULTS.md`: summary and limitations.
- `scenario_statistics.csv`: time, variability and added index space.
- `incremental_comparisons.csv`: INCLUDE versus plain date indexes.
- `query_statistics.csv`: improvements and regressions by query.
- `output_validation.csv`: output differences requiring review.
- `01_performance_storage.svg` and `02_run_stability.svg`: report figures.

SQL, hashes, estimated plans and index inventories accompany the measurements.
Review `REVIEW_REQUIRED` outputs before accepting conclusions. Output checks do
not prove formal SQL equivalence; estimated plans do not establish actual costs.
Small timing differences require confirmation against variability.

Protocol `cci_main_v3` requires the main optimized source schema. Earlier protocol
results and measurements using INCLUDE without `ss_item_sk`
are not results of this design. New SQL Server measurements are required.

To resume an interrupted measurement, repeat the run command with
`--resume benchmark_results/optimization_ID/findings_1GB_ID`. Code, SQL, scale
and selection must match. If backup or restore failed, start a new run.

Commit the experiment, optimized schema files, loader and Compose mount to GitHub.
Share generated results
separately; exclude data, backups, credentials and `.venv`.

## Local checks

```sh
bash -n experiments/optimization_benchmark/run.sh
bash -n experiments/optimization_benchmark/benchmark.sh
```

Live SQL Server measurements are required to validate performance.
