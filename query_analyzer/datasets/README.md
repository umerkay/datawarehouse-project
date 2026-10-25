# Shared diagnostic inputs

Execution plans are stored in one `plans.zip` per size, alongside timing summaries.
No raw `.dat` datasets,
database backups, credentials, or local recovery logs are needed for the website.

| Size | Captured plans | Historical timing rows |
| --- | ---: | ---: |
| 1GB | 103 | 103 |
| 2GB | 103 | 103 |
| 5GB | 103 | 103 |
| 10GB | Pending contribution | 103 |

The existing XML plans are estimated SQL Server plans from this project's TPC-DS
querysets. The original `.dat` files were removed to save disk space; the plans
remain valid recorded evidence and the site does not query a database.

The 5GB archive was captured on 2026-10-25 (UTC; 2026-10-26 local time) from
`tpcds_5`, using SQL Server 2022 version 16.0.4305.5, compatibility level 160,
with automatic statistics creation and updates enabled. All 25 input files were
verified against their original SHA-256 manifest in the retained Docker cache.
The partial database load was completed; all 25 table row counts matched the
manifest and the original foreign keys were validated before capture. The 24
existing indexes were retained; no optimization indexes were added. The table
definitions were preserved. The capture used `SET SHOWPLAN_XML ON` for the 103
files listed in `data/5GB/queryset/queries.lst`; the SELECT queries were not
executed. Each original XML was validated before packaging in `5GB/plans.zip`.
These new plans are independent of the historical timing measurements below.

The timing CSVs were extracted from `results/Baseline Results.zip`:

- 1GB: `tpcds_01_20261018T170745Z/summary.csv`
- 2GB: `tpcds_2_20261018T174002Z/summary.csv`
- 5GB: `tpcds_5_20261018T181654Z/summary.csv`
- 10GB: `tpcds_10_20261018T184553Z/summary.csv`

These are historical timings, not fresh measurements made while building the
site. Joins use filename stems such as `query_015`; they are not verified against
a historical SQL fingerprint. `query_015` identifies the file/list entry, not
necessarily official TPC-DS query number 15.

Contribution layout:

```text
datasets/5GB/
  plans.zip             # shared archive containing original query_*.xml/sqlplan
  timings.csv
  # Local capture folder, ignored by Git:
  plans/
    query_001.sqlplan
    query_002.sqlplan
    ...
```

Use either `.xml` or `.sqlplan` per query, not both with the same filename stem.
Keep scale-specific plans separate. Run `python -m query_analyzer pack 5GB` after
capturing files, then commit the archive. The website reads ZIPs in memory without
extracting them; original XML bytes and filenames are retained.
See `../CONTRIBUTING.md`.
