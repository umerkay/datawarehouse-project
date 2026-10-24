# TPC-DS join analysis

Three complementary views help shortlist index experiments:

1. **Syntax**: table/column relationships and distinct-query frequencies without
   a database connection.
2. **Estimated plans**: SQL Server's proposed operators and cardinalities without
   executing the workload.
3. **Actual plans**: bounded diagnostic executions, measured elapsed/CPU time,
   actual join output and estimation errors.

None automatically recommends or creates indexes.

## Source versus generated files

```text
tools/join_graph/
  analyze_workload.py     # Syntax-analysis CLI and workload selection
  extractor.py            # Thin SQLGlot scope/predicate adapter
  collect_plans.py        # Estimated or opt-in actual plan collection
  plan_report.py          # XML parsing, metrics and plan reports
  compare_plans.py        # SQL-hash-checked snapshot comparison
  report.py              # Shared aggregation and exports
  graph.html             # Source template, not a generated graph
  requirements.txt       # Pinned parser dependency
  samples/               # Small hand-written SQL fixtures + expected.json
  tests/                 # Unit/integration test source
  generated/             # ALL generated artifacts; Git-ignored
    static/
    plans-baseline/
    plans-indexed/
    plan-comparison/
    actual-baseline/
    actual-indexed/
    actual-comparison/
```

**Commit only the source, tests, samples and documentation.** Generated HTML,
SVG, XML plans, CSV/JSON reports, manifests, query copies and stdout/stderr belong
under `generated/`, which is ignored recursively. `.venv/` and bytecode are also
ignored. Old top-level output directory names are ignored defensively but no
longer used by the documented commands. No output files are needed in a fresh
checkout; regenerate them with the commands below. Custom `--output` paths should
also be under `generated/` or another Git-ignored location.

## Setup and static graph

Python 3.10+; from the repository root:

```bash
python3 -m venv tools/join_graph/.venv
source tools/join_graph/.venv/bin/activate
python -m pip install -r tools/join_graph/requirements.txt
python tools/join_graph/analyze_workload.py --fail-on-parse-errors
```

Open [generated/static/join_graph.html](generated/static/join_graph.html) in a
normal browser. It is self-contained and offline-capable; query evidence links
work when the output directory is kept together. Also available:
[static SVG](generated/static/join_graph.svg),
[result summary](generated/static/README.md) and
[coverage](generated/static/coverage_report.json).

Defaults: input `data/1GB/queryset`, schema `tpcds-kit/tools/tpcds.sql`, output
`tools/join_graph/generated/static`. Only filenames listed in `queries.lst` are
analyzed; `benchmark.sql` is not double-counted. Without a list, recursively select
`.sql` files except `benchmark.sql`. IDs are relative paths including `.sql`;
paths with spaces are supported. Duplicate list entries are warned/deduplicated,
missing listed files are failures, and paths outside input are rejected.

Static options: `--input`, `--output`, `--query-list` (list path relative to current
directory; entries relative to input), `--schema`, `--no-schema`,
`--min-edge-query-count` (default 5), `--fail-on-parse-errors`, `--verbose`.
Exit codes: 0 reports written, 1 strict mode with query failures, 2 setup error.
Empty input produces valid empty reports. Per-query failures do not abort the
workload; unhandled programming errors are not silently swallowed.

## Estimated plans (no workload execution)

Requires running `tpcds_sqlserver`, loaded databases and Docker access:

```bash
source tools/join_graph/.venv/bin/activate
python tools/join_graph/collect_plans.py \
  --database tpcds_01 \
  --output tools/join_graph/generated/plans-baseline --timeout 20
python tools/join_graph/collect_plans.py \
  --database tpcds_01_indexed \
  --output tools/join_graph/generated/plans-indexed --timeout 20
python tools/join_graph/compare_plans.py \
  --baseline tools/join_graph/generated/plans-baseline \
  --indexed tools/join_graph/generated/plans-indexed \
  --output tools/join_graph/generated/plan-comparison
```

View [baseline](generated/plans-baseline/join_graph.html),
[indexed](generated/plans-indexed/join_graph.html), and
[comparison](generated/plan-comparison/README.md).
`SET SHOWPLAN_XML ON` compiles but does not execute the workload SELECTs.
Compilation can consume CPU, acquire schema locks or trigger statistics work;
do not collect plans during timed benchmarks.

## Actual plans and estimation accuracy (executes SELECTs)

**`--actual` explicitly executes each listed query once.** This warms caches,
consumes resources and may hit the per-query deadline. It is not the project's
six-run benchmarking protocol. Queries execute sequentially, not concurrently.
No indexes, table data or benchmark scripts are intentionally modified.

```bash
python tools/join_graph/collect_plans.py \
  --database tpcds_01 --actual --timeout 10 \
  --output tools/join_graph/generated/actual-baseline
python tools/join_graph/collect_plans.py \
  --database tpcds_01_indexed --actual --timeout 10 \
  --output tools/join_graph/generated/actual-indexed
python tools/join_graph/compare_plans.py \
  --baseline tools/join_graph/generated/actual-baseline \
  --indexed tools/join_graph/generated/actual-indexed \
  --output tools/join_graph/generated/actual-comparison
```

View the **[actual baseline graph](generated/actual-baseline/join_graph.html)**,
**[actual indexed graph](generated/actual-indexed/join_graph.html)**, and
[actual comparison](generated/actual-comparison/README.md).

Collection uses `SET STATISTICS XML ON`. SELECT/WITH files only: SELECT INTO,
sqlcmd directives and GO batches are rejected. The collector uses `docker exec
-i` and the existing container's sqlcmd/password environment, without printing
credentials or including a password in argv. Optional flags: `--input`,
`--query-list`, `--container`, `--timeout` (1–300 seconds), `--verbose`.

Each file uses a separate connection. sqlcmd's timeout requests cancellation;
container-side `timeout` terminates the client after another 5 seconds (with
5 seconds kill grace); the host Docker client deadline is timeout + 20 seconds.
Disconnecting cancels the session; cancellation/cleanup can take additional
server time. This bounds collection, not a guarantee of instantaneous server
cleanup. No unrestricted execution fallback is used.

A timeout/failure is checkpointed, collection continues, and the final collector
exit code is 1 if any file failed (2 for setup errors). Timed-out queries have
**no invented actual plan or server timing**; they still appear in manifests,
failures, coverage, the query table and the frequency denominator. A longer
`--timeout` or a small `--query-list` can target them later. Use a new ignored
output directory to preserve prior diagnostics. Raw SELECT result rows are not
saved as report artifacts; raw XML and stderr diagnostics are retained.

### What “actual cost” and deviation mean

SQL Server's estimated subtree cost is in **optimizer units, not milliseconds**.
It has no corresponding actual elapsed-time cost in the same units. A percentage
of milliseconds divided by optimizer cost would be meaningless, so it is not
reported. Instead the graph shows:

- **Query actual elapsed/CPU ms** from `QueryTimeStats`, beside estimated optimizer
  cost. Client elapsed time is separate and includes Docker/sqlcmd, compilation,
  transfer and cleanup. One diagnostic run is not a performance average.
- **Operator actual total rows**, raw thread counters, max worker execution count,
  actual rows per invocation and the estimate from the **same actual plan**.
  Historical estimated-plan NodeIds are not matched to a changed execution plan.
- **Signed row deviation** = `100 × (actual per invocation − estimate) / estimate`.
  Positive = underestimated; negative = overestimated.
- **Row-error factor** = `max(actual / estimate, estimate / actual)`. One is exact,
  ten means a tenfold error in either direction. One zero versus a positive value
  is explicitly *unbounded* with a null finite factor; both zero give factor one.
- **Worst query join error**, including unmapped intermediate joins, to identify
  which queries warrant investigating statistics, skew, predicates or indexes.

For repeated Nested Loops inner invocations, cumulative rows must not be compared
directly with a per-invocation estimate. We sum ActualRows over threads and divide
by **maximum ActualExecutions across workers**, not the sum (parallel workers
process parts of the same invocation). This avoids dividing parallel totals by
DOP or treating repeated invocations as estimation error. Raw counters remain
available: unusual worker imbalance or invocation patterns require manual review.
Branches with zero executions (e.g. adaptive alternatives) have no error score.
Missing counters are unavailable, never zero.

Edge min/max/avg summarize unique associated operators; table-pair summaries
deduplicate operators shared by several join keys. Do not divide aggregate actual
and estimated averages to infer an operator error. Edge details and per-query
reports expose individual identities and deviations. Operator elapsed times can
overlap and include descendants; they are not summed to calculate query time.

## Graph controls

- Table-pair view for topology; joined-attributes view for individual column pairs.
- Node size = distinct referencing queries; edge width = distinct joining queries.
  Occurrence counts remain separate. Gold = facts, blue = physical dimensions,
  purple/dashed = unresolved CTE/derived endpoints in the syntax view.
- Minimum frequency filter and table search; CSV/JSON remain complete.
- Plan graphs rank by estimated rows; actual graphs additionally rank by worst
  row error. Click edges for strategies, estimates, actuals and supporting query
  runtime. The **query runtime table below the graph** includes all selected
  queries, including timeouts, and sorts by error or measured elapsed time.
- Click query table rows for signed deviation, status and raw report links.
  XML links open the retained `.sqlplan`; these can also be inspected in SSMS.
- Scroll to zoom; drag background to pan. The ranked table helps select dense edges.

For a local browser preview:

```bash
python -m http.server 8765 --bind 127.0.0.1 --directory tools/join_graph/generated
# Visit endpoints: /static/join_graph.html, /actual-baseline/join_graph.html, /actual-indexed/join_graph.html
# Stop with Ctrl-C.
```

## Extraction boundaries

The syntax extractor uses pinned SQLGlot, not a custom SQL parser. SQLucent was
evaluated: its JSON describes data-flow nodes and SQL operation strings rather
than the normalized physical-column join records needed here.

Supported: scoped aliases, schema-backed unqualified columns, direct equality
and inequality comparisons in ON/WHERE, comma joins, nested SELECT scopes,
straightforward correlation, self-join roles, set-operation branches, and a
simple explicit two-table CROSS JOIN. Reversed comparisons canonicalize to the
same relationship. Projection comparisons and same-role filters are not edges.

No recursive CTE/derived lineage: physical internal joins are extracted, external
non-physical endpoints are query-local and flagged. Ambiguous columns, expressions,
scalar/IN subqueries, column-based BETWEEN and unsupported APPLY/USING remain
visible as unresolved records. OR/NOT edges are marked conditional, not guaranteed
per-row relationships. Identifiers are lowercased for the usual case-insensitive
SQL Server workload. Schema-qualified and unqualified names are not merged.
Success means no flagged limitations, not proof of complete SQL semantics.

Plan extraction uses explicit Hash/Adaptive keys, Merge keys, direct join
predicates and parameterized Nested Loops seeks. It never generates a Cartesian
expansion of the tables in two input subtrees. All join operators are retained,
including unmapped computed/intermediate endpoints. Optimizer rewrites,
elimination, decorrelation, adaptive alternatives and inferred equalities mean
plan counts need not match syntax counts. A mapped pair is evidence of that
predicate, not proof of complete lineage. Estimates and strategy alternatives
alone are not actual runtime evidence.

Statement session settings, optimizer early-abort reasons, execution mode,
adaptive thresholds, input estimates and table/index accesses are retained.
Optimizer `TimeOut` means search-budget termination, **not** client timeout or
query execution failure. Workload compilation uses the profiler's sqlcmd session
defaults; the separate metadata connection enables QUOTED_IDENTIFIER to read XML
metadata. Match settings/data/statistics when comparing to later runs.

## Output schemas and regeneration

All output directories contain:

| Output | Evidence |
|---|---|
| `manifest.json`, `coverage_report.json` | Every selected query/status; denominators, failures, limitations and report paths. |
| `query_reports/<hash>.json` | Original SQL, scoped predicates/roles, references, failures; plan reports also contain statements and all operators. |
| `normalized_joins.jsonl` | Individual occurrence records with endpoints, types and evidence. |
| `aggregate.json` | Nodes, attribute edges, table pairs, memberships and coverage, embedded into HTML. |
| `join_edges.csv`, `table_pair_edges.csv` | Distinct-query counts, occurrences, columns/types, resolution, full supporting query IDs. |
| `table_nodes.csv`, `query_table_references.csv` | Reference/join counts, node degree and deduplicated query memberships. |
| `unresolved_joins.csv`, `failures.csv` | Explicit unresolved candidates and file failures, including queries without a graph edge. |
| `join_graph.html`, `.svg`, `.dot`, `README.md` | Interactive graph, static pair overview, full physical multigraph and summary/next steps. |

Plan output additionally contains:

| Output | Evidence |
|---|---|
| `plan_manifest.json` | Mode, collection time, query hashes, timeout, server/database settings, index/statistics inventory and raw paths. |
| `raw_plans/` | `.sqlplan` XML, stdout plan text and stderr diagnostics (all ignored). |
| `join_operators.csv` | Every join, including unmapped ones; estimates, runtime counters/deviation when actual, evidence identities. |
| `query_metrics.csv` | Status, optimizer cost, actual server elapsed/CPU, client elapsed, worst join error/direction/identity. |
| `plan_edge_metrics.csv`, `plan_pair_metrics.csv` | Unique-operator min/max/avg estimates, actuals and worst error/strategy summaries. |
| `optimizer_early_aborts.csv` | Valid plans with optimizer search early termination. |

CSV list/dict fields are JSON. `query_count` is distinct files, not statements or
occurrences. Pair query counts are unions, not sums of column-edge counts.
Frequency denominator is **all selected files, including failures**. A physical
self-join contributes once per occurrence to node participation; degree excludes
the node itself. IDs, ordering and outputs are deterministic for fixed inputs,
options and captured XML. New live executions can legitimately differ.

Regenerate graphs offline without re-executing queries:

```bash
python tools/join_graph/plan_report.py --output tools/join_graph/generated/actual-baseline
python tools/join_graph/plan_report.py --output tools/join_graph/generated/actual-indexed
```

Comparison reports require matching original SQL hashes and completed plans;
mismatches and failures are explicitly excluded, not filled with successes.
SQL hashes do not establish identical database contents; contents were not hashed.

## Tests and small sample

```bash
python -m unittest discover -s tools/join_graph/tests -v
RUN_TPCDS_INTEGRATION=1 python -m unittest discover -s tools/join_graph/tests -v
python tools/join_graph/analyze_workload.py \
  --input tools/join_graph/samples \
  --output tools/join_graph/generated/sample --min-edge-query-count 1
```

The normal unit suite uses synthetic SQL/XML and needs no live database or network.
The optional integration test reads the locally generated workload and skips when
unavailable. The sample's [expected.json](samples/expected.json) proves, among
other things, that the item/store_sales edge occurs in 2 queries but 3 predicates.
Tests cover scoped extraction, canonicalization, failures, export reproducibility,
parallel/repeated runtime counters, zero/unexecuted branches, signed error metrics,
timeout continuation and result-row omission.

The current implementation passed **62 tests**, including the optional local
workload test. The bounded actual capture completed **99/103** baseline queries
and **103/103** indexed queries. Baseline query_010, query_014, query_015 and
query_076 reached the 10-second limit; none has fabricated server timings or row
errors. Independent checks verified all **1,542** retained join operators against
raw XML, all query elapsed times and edge/pair runtime summaries. Offline
regeneration was byte-identical, and browser checks covered runtime/error
ranking, query details, timeout visibility, XML links and the original static view.
These observations describe this diagnostic snapshot, not a performance guarantee.

## Next steps for the benchmarking project

1. Use syntax frequency plus the actual graph's query/error rankings to shortlist
   hypotheses. Large cardinality errors can indicate stale statistics, skew,
   correlations or complex predicates; **an index is not automatically the fix**.
2. Inspect the linked whole plan, especially unmapped intermediate joins, filter
   estimates, chosen index keys and spills. Check existing clustered primary keys
   and [indexing_strategy.sql](../../scripts/indexing_strategy.sql) before creating
   duplicate indexes. Separate workload importance from estimator accuracy.
3. Timeouts (historically query_015/query_082 may be slow) are censored observations,
   not fast completions. Investigate selected files with a deliberate longer limit;
   do not silently exclude them from a claimed full-workload speedup.
4. Test a small candidate index/statistics change on a separate database. Keep
   data, statistics, server conditions and query text comparable. Diagnostic actual
   plans add overhead and warm caches; use them for explanation, not final timings.
5. Run the existing profiler for focused confirmation and the unchanged full
   six-run benchmark for the final baseline/indexed comparison. Do not measure
   concurrent collection, index creation or statistics maintenance.
