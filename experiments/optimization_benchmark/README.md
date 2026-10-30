# Reproducible TPC-DS optimization experiment

Run from the project repository on the machine hosting SQL Server, the databases
and querysets. This folder contains all experiment code; it does not depend on
the web application or external analysis scripts. It produces CSV, PNG, SVG and
a Markdown summary, without generating a website.

## Configurations

| Identifier | Indexes added to the baseline |
|---|---|
| `baseline` | No additional secondary indexes. |
| `date_keys` | Nonclustered indexes on `ss_sold_date_sk`, `cs_sold_date_sk` and `ws_sold_date_sk`. |
| `date_shared` | The same indexes; store_sales adds `INCLUDE (ss_customer_sk, ss_quantity, ss_sales_price)`. |
| `date_priority` | The same indexes; store_sales adds `INCLUDE (ss_quantity, ss_list_price)`. |
| `columnstore` | One nonclustered columnstore index covering all columns on each of store_sales, catalog_sales and web_sales. |
| `columnstore_date_keys` | All three columnstore indexes plus the three plain date-key indexes, **without INCLUDE**. |

Existing primary keys are retained. The catalog_sales and web_sales date-key
indexes never have INCLUDE columns. This combined configuration differs from
the historical `combined` scenario used in the previous experiment.

## Quick start for project teammates

Use your existing project checkout, Docker Compose service, `.env`, databases
and generated querysets. **No Docker setup, data generation or reload is needed.**
After pulling this folder, run the command below from the repository root:

```sh
bash experiments/optimization_benchmark/run.sh --scale 1GB --database tpcds_01
```

Replace `tpcds_01` if your existing baseline has a different name. The launcher
prepares only a local Python environment for analysis, runs the six scenarios
on a new private database copy and automatically generates CSV and figures.
Use Python **3.12 or later** (validated with 3.14); pinned dependencies are
installed from PyPI on the first run. Their versions are pinned directly in `run.sh`
(`matplotlib==3.11.2`, `numpy==2.5.4`); no requirements file is needed.

### Before starting

- Ensure the existing `sqlserver` service is running and the selected database
  matches the scale. Keep using the project's existing credentials and mounts.
- Use the original baseline, without optimization indexes on the three sales
  tables. Primary keys and unique constraints may remain. The runner rejects
  a baseline containing secondary indexes on those tables.
- The existing `data/<scale>/queryset/queries.lst` selects the workload, expected
  to contain the project's 103 queries. It is reused without regenerating data.
- Check space for a backup, private database copy, indexes, log growth and output
  files. The runner does not remove existing databases or backups to make room:

```sh
docker compose exec -T sqlserver df -h /var/opt/mssql /benchmark_results
```

Do not run other benchmarks, data loads or configuration changes concurrently.
Prevent computer sleep and Docker restarts during execution. Depending on scale
and hardware, the experiment may take several hours.

## Execution options

From the repository root, using Linux/macOS or Git Bash:

```sh
bash experiments/optimization_benchmark/run.sh --scale 1GB --database tpcds_01
```

The existing SQL Server service and datasets are reused. The launcher does not
rebuild containers, change Compose settings or install Python inside SQL Server.

On Windows, use Git Bash (included with Git for Windows) to run the same command.
The launcher detects the Windows virtual environment layout automatically.

On Linux, `.venv/bin/python` can also be used directly. For another scale, change
both `--scale` and `--database`; run scales separately. The runner does not infer
scale from database contents, so verify that the database matches the label.
If the Compose service has another name, add `--service SERVICE_NAME`.

To check query selection and container access without executing SQL:

```sh
bash experiments/optimization_benchmark/run.sh --scale 1GB --database tpcds_01 --dry-run
```

A dry run copies two temporary files into the container but does not connect to
SQL Server, create databases or measure queries. It does not verify SQL
permissions or sufficient disk space.

The existing container must expose sqlcmd at `/opt/mssql-tools18/bin/sqlcmd`
and the usual project mounts (`/data` and `/benchmark_results`). It uses the
project's SQL credentials for backup, restore, index creation and plan capture.

## Protocol

1. Save an immutable snapshot of the runner and index SQL, with provenance hashes.
2. Create a `COPY_ONLY` backup and restore it to a new private database whose
   name contains `_findings_`. No DDL or configuration changes affect the source.
3. For each configuration, remove only experiment-owned indexes, create the
   requested indexes, compile queries, apply uniform `FULLSCAN` statistics and
   capture normalized estimated plans and index inventories.
4. Run one complete warm-up pass, excluded from measurements, followed by five
   measured passes in the same query order. For 103 queries this produces
   **3,090 measurements** and 618 warm-up outputs, plus estimated plans.
5. Validate query coverage, five distinct measurements, summaries, hashes and
   outputs. Automatically export CSV, figures and `RESULTS.md`.

Durations are measured around each sqlcmd process, including startup and output
transfer. They are not engine-only execution times. The sum of query means equals
the mean of per-pass sums, not the total script duration. Index creation,
FULLSCAN and plan capture are excluded from query measurements. Caches are not
cleared and SQL Server is not restarted between configurations.

## Results and report figures

```text
benchmark_results/optimization_<UTC_timestamp>_<id>/
  execution_*.log
  invocation_*.json
  .runner_versions/               # immutable effective runner snapshots
  findings_<scale>_<id>/
    manifest.txt, query_sha256.txt, queries.lst, source_queries/
    source_metadata.txt, rowcounts.csv, clone.log, STATUS
    baseline/, date_keys/, date_shared/, date_priority/,
    columnstore/, columnstore_date_keys/
      timings.csv, summary.csv, indexes.csv, index_columns.csv
      result_checks.csv, plans/*.xml, results/*.txt.gz
    analysis/
      RESULTS.md
      scenario_statistics.csv     # main report table
      query_statistics.csv        # per-query means, SD, regressions and speedups
      pass_totals.csv             # five duration sums per configuration
      incremental_comparisons.csv # INCLUDE vs plain keys; combination vs components
      output_validation.csv       # output differences requiring review
      estimated_index_usage.csv   # queries whose plans select each index
      statistics.json, SHA256.csv, STATUS
      01_performance_storage.png/.svg
      02_run_stability.png/.svg
      03_query015.png/.svg
      04_query_speedups.png/.svg
```

For the report, use `scenario_statistics.csv` and figure 01. Include figure 02 in
the methodology or appendix to show stability, figure 03 for query_015, and figure
04 to compare improvements and regressions among the slowest baseline queries.
PNG files use 220 dpi; SVG files preserve editable text. All labels are in English.
All results are calculated from the new run; historical measurements from another
machine are not included. Website screenshots are unnecessary.

`analysis/STATUS` reports `COMPLETE` or `REVIEW_REQUIRED` after successful export.
Unresolved output differences produce exit code 2 while preserving tables and
figures. Review `output_validation.csv` before treating those times as evidence
of improvement. SQL errors stop execution rather than producing an analysis
presented as complete. Intermediate analysis states include `VALIDATING`,
`VALIDATION_FAILED` and `GENERATING_OUTPUTS`.

## Interruptions and regenerating outputs

After an interruption, retain the output folder and private database. Resume by
passing the specific `findings_...` folder, not `analysis` or its parent:

```sh
bash experiments/optimization_benchmark/run.sh --scale 1GB --database tpcds_01 --resume benchmark_results/optimization_TIMESTAMP_ID/findings_1GB_TIMESTAMP_ID
```

The runner validates selection, query SQL and index definitions, preserves
complete phases and restarts incomplete phases with a fresh warm-up. Partial
samples are not mixed. Interruptions are documented: a baseline collected before
a restart may be less comparable, so prefer a clean run for final conclusions.
Do not use `--resume` to add scenarios or change index definitions.

To regenerate tables and figures without executing SQL:

```sh
python experiments/optimization_benchmark/analyze.py benchmark_results/optimization_TIMESTAMP_ID/findings_1GB_TIMESTAMP_ID
```

Use the prepared environment's Python interpreter. Analysis rejects incomplete
phases, duplicate samples, inconsistent summaries and output hash mismatches.

## Interpretation and sharing

- Error bars show one sample SD in each direction across five passes, not
  confidence intervals. Small differences alone do not establish significance.
- Rendered-row comparison uses warm-up outputs, preserves duplicates and
  separates result sets. It distinguishes ordering changes and the standard
  NULL aggregation warning; values are not rounded to force equality. This is
  not a formal SQL equivalence proof and does not validate required ordering.
- Plans are **estimated**, not actual. Index selection indicates expected use,
  not measured operator cost or causal attribution of execution time.
- Configurations run in fixed order. Inspect variability and trends. Writes and
  index maintenance costs are not evaluated. Do not extrapolate across scales.
- Retain the entire `optimization_...` folder for auditing. Keep the private
  database and backup if further SQL inspection is needed; neither is deleted
  automatically.
- For GitHub, upload **this code and documentation folder**. Virtual environments,
  results, databases, backups and secrets are not part of the package. CSV and
  image outputs are saved under the Git-ignored `benchmark_results` directory;
  share them as a ZIP or release attachment. Review logs for sensitive data
  before sharing them.

## Files

| File | Purpose |
|---|---|
| `run.sh` | Prepare Python, coordinate Docker, save logs and provenance, and generate results automatically. |
| `benchmark.sh` | Restore the private database, apply scenarios, measure queries and handle resume. |
| `indexes.sql` | Define the six index configurations on the private database only. |
| `validation.py` | Validate measurements and outputs; calculate descriptive statistics. |
| `analyze.py` | Export CSV, PNG, SVG, JSON and the Markdown results summary. |
| `.gitignore` | Exclude the virtual environment and Python temporary files. |
| `.gitattributes` | Preserve Linux-compatible line endings. |
