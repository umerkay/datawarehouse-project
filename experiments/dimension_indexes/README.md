# Dimension indexes on a CCI baseline

Does indexing dimension join/filter columns improve the same workload when all
seven fact tables already have clustered columnstore indexes? This experiment
adds **nine independent single-column B-tree indexes**, with no INCLUDE, composite
keys, hints or changes to facts, SQL queries or original constraints.

| Scenario | Additional indexes |
|---|---|
| `baseline` | None; existing seven fact CCIs and original PKs/constraints. |
| `all_dimensions` | All nine dimension indexes together. |

The nine indexes are: `customer(c_current_addr_sk)`;
`customer_address(ca_state)` and `(ca_gmt_offset)`;
`customer_demographics(cd_education_status)` and `(cd_marital_status)`;
`date_dim(d_year)`, `(d_moy)`, `(d_date)` and `(d_month_seq)`.
`c_current_addr_sk` is the schema name for the supplied `current_addr_sk`.

After measuring the combined effect against baseline, inspect `index_review.csv`
to find indexes selected in estimated plans and candidates for removal. It lists
all nine, including indexes with zero references, their query identifiers and
individual storage. Nothing is automatically discarded. Zero estimated use is
evidence for this workload only; confirm removals with a follow-up benchmark.
The supplied query-frequency counts motivate the candidates; they are not
assumed to be usage counts in every generated queryset.

## Run

Requires Python 3.10+, Docker, a running `tpcds_sqlserver` container with its
existing `MSSQL_SA_PASSWORD`, and split SQL files in `data/1GB/queryset/`.
The runner uses only Python's standard library; plotting packages are needed only
for the notebook. Run commands from the repository root on Windows or Linux.

Use a **clean, populated CCI database** created with
`tpcds-optimized/tools/tpcds.sql` and its original constraints. The runner checks
for seven fact CCIs, nonclustered fact PKs, clustered dimension PKs, and absence
of other tuning indexes/indexed views. It rejects the old rowstore/NCCI report
database. It does not create or convert CCIs, and never executes the unrelated
`scripts/indexing_strategy.sql` materialized-view strategy.

```powershell
# Validate arguments/query files; no Docker or SQL connection and no output files.
python experiments/dimension_indexes/run.py --database YOUR_CCI_DB --scale 1GB --dry-run

# Run both scenarios. Replace the database name with your existing CCI source.
python experiments/dimension_indexes/run.py --database YOUR_CCI_DB --scale 1GB
```

For other sizes, repeat with the relevant `--database` and `--scale 2GB` or
`--scale 5GB`. **All scales use the 1GB SQL by default**, so only the data changes.
Use `--query-scale` or `--queries PATH` to choose a different queryset explicitly.
`--container`, `--timeout` and `--passes` override defaults. Keep five measured
passes for the report. Run one experiment at a time, with no concurrent loads.

The runner creates a COPY_ONLY backup and restores a private
`SOURCE_dimexp_TIMESTAMP_ID` database. **Source data/indexes are never modified.**
The private copy and backup remain available for inspection; allow disk space
for both and all nine indexes. Their name/path is recorded in the run manifest
and `clone.log`. There is no automatic database/file deletion or resume. A failed
run retains diagnostics and is excluded from analysis; rerun after fixing the
cause. Check Docker/SQL Server health first, particularly after a tempdb I/O error.

## Measurements

- Two scenarios, one excluded warm-up and five measured full-workload passes:
  **1,030 measurements with the usual 103 queries per scale**.
- Scenario order is shuffled reproducibly (`--seed 20261106` by default).
  Repeat with another seed to check sensitivity to execution order; one shuffled
  run alone does not eliminate time/order effects.
- Exact SQL bytes and runner/index definitions are snapshotted and hashed.
- Compile queries, update all table statistics with FULLSCAN and clear only the
  private database's procedure cache before each scenario. Capture normalized
  estimated plans, then warm up. Data buffers stay warm; no global cache clearing.
- Index setup, statistics and plan capture are outside query timing.
- Timings include Docker/sqlcmd startup and fetching rendered output, measured
  consistently across scenarios. They are elapsed client times, not pure SQL CPU.
- Check saved warm-up outputs against baseline. Row-order and NULL-warning-only
  differences are identified separately; `REVIEW_REQUIRED` needs inspection.
  Rendered output comparisons are not a formal SQL-equivalence proof.
- Plans show **estimated index selection**, not actual reads/execution counts.
  The notebook displays zero-use indexes too. Original index inventories are
  checked for changes; no forced index selection is used.

Results go to the ignored folder
`benchmark_results/dimension_indexes/SCALE_TIMESTAMP_ID/`. `STATUS`, `error.txt`
(on failure), `manifest.json`, `setup.csv`, per-scenario `timings.csv`, plans,
outputs, inventories and `analysis/index_review.csv` accompany the generated `analysis/*.csv` tables.

## Notebook

```powershell
python -m venv experiments/dimension_indexes/.venv
./experiments/dimension_indexes/.venv/Scripts/python.exe -m pip install -r experiments/dimension_indexes/requirements.txt
./experiments/dimension_indexes/.venv/Scripts/jupyter-lab.exe experiments/dimension_indexes/results.ipynb
```

On Linux use `.venv/bin/` instead of `.venv/Scripts/`. Select that environment in
VS Code/Jupyter and run all cells. Set `RUN_DIRS` in the notebook when comparing
specific runs. Otherwise it discovers completed runs and requires explicit
selection if more than one exists for the same scale. It validates measurements,
hashes and outputs before plotting; it never invents results when no run exists.

The notebook exports workload mean/SD, per-pass stability, added index storage,
query improvements/regressions, output reviews and estimated index usage.
The index review table separates selected indexes from candidates for removal. Tables
and PNG/SVG figures go to the ignored `exports/` directory. Compare each scale
against its own baseline. SQL hash differences across scales are flagged; small
differences relative to variability do not establish a speedup or significance.

Tables can also be regenerated without Jupyter:

```powershell
python experiments/dimension_indexes/analyze.py benchmark_results/dimension_indexes/RUN_FOLDER
python -m unittest discover -s experiments/dimension_indexes/tests -v
```
