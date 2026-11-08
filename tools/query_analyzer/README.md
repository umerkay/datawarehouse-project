# TPC-DS optimization radar

An offline, static website for reviewing SQL Server execution plans and comparing
TPC-DS queries across 1GB, 2GB, 5GB and 10GB. The interface is in English.

## Open the website

From the repository root, with **Python 3.10 or newer**:

```bash
python -B -m tools.query_analyzer build
python -m http.server 8000 --directory tools/query_analyzer/site
```

Open **http://localhost:8000/**. Alternatively, open
`tools/query_analyzer/site/index.html` directly or with VS Code Live Server.

The build uses only Python's standard library. No pip installation, SQL Server,
Docker, credentials, internet connection or raw `.dat` datasets are required to
view existing results. HTML contains the data before JavaScript runs; scripts
only enhance navigation, search and filtering. CSS/JS assets are local.

**Open `site/index.html`, not a file in `web/templates/`.**

In VS Code, you can also right-click **`tools/query_analyzer/index.html`** and choose
**Open with Live Server**. This small, versioned entry page redirects to
`site/index.html`. After a fresh clone, run the build command above first.

## Current content

| Size | Available plans | Historical timing rows |
| --- | ---: | ---: |
| 1GB | 103 | 103 |
| 2GB | 103 | 103 |
| 5GB | 103 | 103 |
| 10GB | Pending contribution | 103 |

Missing plans get a real pending page, not an empty dashboard or plans borrowed
from another size. The overview, all four dataset reports, comparison page and
JSON exports are rebuilt together. All navigation links have generated targets.

Original plans are grouped into **one `plans.zip` per size**; those archives and
the timing CSVs in `datasets/` are included in Git so a fresh clone
can reproduce the website. See [data provenance](datasets/README.md). Generated
HTML/JSON in `site/`, local inputs, credentials, large data and old operational
scripts are excluded.

## Add the 5GB and 10GB plans

Follow [CONTRIBUTING.md](CONTRIBUTING.md): export `.sqlplan` or XML files into the
appropriate local `datasets/<size>/plans/` directory, preserve filename stems such
as `query_015`, run `python -m tools.query_analyzer pack 5GB` (or `10GB`), test, and
rebuild. Commit the resulting `plans.zip`, not the loose files. The current complete queryset has 103 entries.
Partial contributions are supported and their true coverage remains visible.

The optional [capture helper](scripts/capture_plans.sh) connects to an **already
populated** database and requests estimated plans. It never creates/loads a
database or applies indexes/statistics changes. It is separate from the offline
website build and needs Bash, sqlcmd, appropriate SQL permissions and connection
variables supplied by the contributor.

## Structure

The code follows a single flow: **diagnostic inputs ? core analysis ? web rendering
? generated site**. The core never imports the web layer. You can build and test
it without SQL Server or Docker.

```text
tools/query_analyzer/
  __init__.py, __main__.py   # package definition and python -m entry point
  app/                      # commands and site build orchestration
    cli.py                  # public commands: build and pack
    build.py                # coordinates a complete site build
  core/                     # input and analysis; no page rendering
    common.py               # package paths, scale names and atomic file writes
    analysis.py             # SQL Server Showplan parsing and findings
    plan_structure.py       # normalized operator topology
    sources.py              # configuration, plan archives and timing CSVs
  web/                      # everything that renders or styles the website
    common.py               # HTML templates, navigation and pending pages
    dashboard.py            # dataset reports and technical evidence
    comparison.py           # cross-size charts, plan map and query cards
    templates/              # HTML layouts for home, reports and comparison
    assets/                 # CSS and JavaScript for reports and comparison
  datasets/                 # small recorded plans and historical timings
    sources.json            # input paths relative to this folder
  scripts/capture_plans.sh   # optional estimated-plan capture from SQL Server
  tests/                    # parser, rendering, build and input regressions
  site/                     # generated pages and assets; ignored by Git
```

| What to change | Source location |
| --- | --- |
| XML parsing and diagnostic rules | `core/analysis.py` |
| Definition of equivalent plan structures | `core/plan_structure.py` |
| CSV/ZIP input handling and configuration | `core/sources.py` |
| Per-dataset content and technical evidence | `web/dashboard.py` |
| Comparison content and chart generation | `web/comparison.py` |
| HTML layout | `web/templates/` |
| Browser filters, selection and visual styles | `web/assets/` |
| Available datasets and input locations | `datasets/sources.json` |

The package root contains its README, contribution guide, ignore rules,
the two Python package entry files and `index.html` for opening with Live Server.
Earlier root-level HTML/JSON files are kept
in the ignored `inputs/legacy_root/` archive. Open `site/index.html` for the website.

Run `python -B -m tools.query_analyzer build` after editing. Never edit generated
`site/` files: the next build replaces them. Published asset URLs remain
`assets/...`; the build copies files from `web/assets/` into `site/assets/`.

`inputs/` and `local_tools/` are local-only archives, not application dependencies.
The earlier query-015 experiment and machine-specific troubleshooting scripts
remain in `local_tools/`; they are not part of the shared app. There is one CLI:
`python -m tools.query_analyzer`, with `build` and `pack` subcommands. Earlier individual
generator commands have been removed to avoid maintaining duplicate paths.

Within `web/comparison.py`, `render` assembles the page, `query_section` composes a
query's charts, and `plan_card` renders one dataset. `plan_signals` supplies the
same index and warning counts to the cards and workload explorer. Chart helpers
render the duration SVG, operator bars and plan map independently.

`web/assets/comparison.js` initializes three independent parts: query navigation,
plan-map filters and workload ranking. `web/assets/comparison.css` groups their
styles with comments. Keep new behavior in the matching section rather than
adding another script for each graph. The site needs no JavaScript framework,
package installation or bundler.

## Build options and tests

```bash
python -B -m tools.query_analyzer build --config path/to/sources.json --output path/to/site
python -B -m unittest discover -s tools/query_analyzer/tests -v
```

Configuration uses the same four scale keys as `datasets/sources.json`.
Relative paths are resolved against the configuration file, independent of the
current working directory. Existing malformed XML, duplicate query IDs and invalid
CSV timings cause a nonzero build status with visible diagnostics. Intentionally
missing plan folders remain pending. Output files are replaced atomically, so an
interrupted write does not leave a half-written page.

## Share on GitHub

| Include | Keep local (ignored by Git) |
| --- | --- |
| Python code, HTML templates, CSS/JS, tests and documentation | `site/` and the older generated HTML/JSON files |
| `datasets/{1GB,2GB,5GB}/plans.zip` and all four `timings.csv` files | `data/`, original `.dat` files, database storage and backups |
| Configuration, workflows and `.env.example` placeholders | `.env`, `inputs/`, `local_tools/`, loose plan captures |
| Project scripts, Compose files and TPC-DS generator sources | `results/`, `benchmark_results/`, compiled tools and `proyecto*.zip` |

The diagnostic archives and timing CSVs are small inputs used to reproduce the
site; they are distinct from the large raw datasets in `data/`. Only `plans.zip`
and `timings.csv` are allowed inside the four dataset folders. Capture metadata,
temporary exports and other archives stay local. The pending 10GB archive can be
contributed later under the same rules. GitHub Pages builds `site/` in Actions;
generated pages do not need to be committed.

Ignore rules do not remove files already tracked by Git. Before staging, check:

```bash
git status --short
git ls-files --cached --ignored --exclude-standard
git diff --cached --stat
```

The second command should produce no output. Review existing unrelated changes
separately; an ignore rule cannot select which tracked edits belong in a commit.

Commit `tools/query_analyzer/`, the two `query-analyzer-*.yml` workflows, `.gitignore`,
`.gitattributes`, and the root README change. Review your other working-tree changes
separately. Do not force-add ignored local archives or raw datasets.

`Query analyzer checks` runs tests, Bash/JavaScript syntax checks and a full offline
build on pull requests and pushes affecting this tool.

For a browser link through GitHub Pages:

1. Push the reviewed files to your repository.
2. In **Settings > Pages**, choose **GitHub Actions** as the source.
3. In **Actions**, run **Publish query analyzer** manually.
4. Open the URL reported by its deployment job.

Publishing is manual; the workflow is not triggered by normal pushes. This setup
builds the static site from the contributed plan files; no SQL credentials belong
in GitHub Actions. See [GitHub Pages workflow documentation](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages).

## Interpretation limits

### Overall analysis for the report

Open **Overall analysis** from Home or `site/overall.html`. This is the main
cross-dataset view; individual dataset reports are unchanged. It consolidates
observations rather than presenting automatic strategy recommendations:

- Shared sales-date-key MissingIndex requests, with distinct-query counts for
  all four sizes and supporting query links.
- Non-columnstore scans upstream of an aggregate, restricted to at least 10,000
  estimated output rows. Counts match the existing per-dataset observation rule.
- INCLUDE-set counts and union sizes for the date-key patterns, column-frequency
  figures and full-request fit comparisons for each dataset.
- Recurring resolved grouping and sort columns, plus optional scan-filtering and
  NoJoinPredicate evidence. Matching grain alone does not establish reusable data.

Figures have zero-based axes, exact labels and explicit pending states. Download
their standalone SVG files from each figure for insertion into the report, or
capture the figure and its caption. JSON contains the underlying query evidence;
the CSV export contains index counts. Queries are deduplicated within each dataset,
not counted as operator occurrences. Cross-size matching is not a controlled
experiment when SQL or settings differ. The previous detailed index page remains
available from the INCLUDE section; global navigation now emphasizes this overview.

### Choosing optimization strategies

The **Index comparison** page (`site/indexes.html`) reproduces the cross-size
sales-date-key table directly from SQL Server MissingIndex entries. It also
compares all other key patterns and their INCLUDE variants. Counts deduplicate
queries within each size, equality keys are treated as a set and range-key order
is retained. Database names are excluded so the same table can be compared across
sizes. The detail lists exact INCLUDE sets, per-column query frequencies, their
intersection and union. A set satisfies a query's recorded requests only if it
contains every INCLUDE set requested by that query for the same key pattern.
This is a column-request comparison, not verified physical coverage or predicted
performance. No wide union index is automatically recommended. Missing plans are
pending rather than zero. CSV counts and full JSON evidence are downloadable.

Start with **Optimization strategies** (`site/strategies.html`) or the first
section of a dataset report. Findings are built from plans alone; historical
durations are supplementary context and do not drive these rules. Each finding
identifies the table, distinct supporting queries, statement/operator evidence,
an experiment to try and the conditions to check before deciding. SQL Server
missing-index suggestions are labelled separately from analyzer hypotheses.
The rules are:

- **Rowstore indexes:** group SQL Server suggestions by table, equality-key set
  and ordered range keys. Keep INCLUDE variants visible rather than blindly
  combining them into a wide index. Counts deduplicate queries and suggestions.
- **Selective filtering:** require a scan with a recorded residual predicate,
  at least 10,000 estimated rows read and estimated output at most one tenth of
  estimated rows read. Missing read estimates do not imply selectivity.
- **Columnstore:** require a non-columnstore scan with at least 10,000 estimated
  output rows on a descendant branch of an aggregate. A hash join or scan alone
  does not qualify. Review columns, selectivity and index coverage before testing.
- **Pre-aggregation:** require identical resolved grouping columns in at least
  two distinct queries, with aggregate expressions recorded. Filters, measures
  and joins still need manual compatibility checks; common grain alone is not
  proof that results can be reused. Computed/unresolved grouping keys are omitted.
- **Ordering:** require the same resolved sort columns, sequence and direction
  in at least two distinct queries. An index providing that ordering may still
  lose it through joins or parallel exchanges.

The row thresholds are explicit screening heuristics, not measured bottlenecks.
Candidates are grouped by strategy family and sorted by query coverage, not by a
fabricated benefit score. The 10GB dataset remains pending until plans exist.
Structured findings are also included in each `analysis*.json` export.
The optional baseline/indexed page is retained at `site/experiments.html` for
later validation; the main navigation prioritizes plan-based strategy selection.

### Plan inspection and indexing experiments

Each query's technical evidence includes an expandable execution-plan tree.
Branches preserve the XML operator topology. Nodes show table/index access,
estimated output rows, local estimated cost, predicates and output columns.
Seek predicates and residual filters retain their XML labels; properties are
scoped to their own operator rather than inherited from child operators.
The row-estimate mismatch filter is only offered when actual plans are present.

To compare missing-index suggestions with existing indexes, run
`scripts/export_indexes.sql` in the database whose plans were captured and
export the result as UTF-8 CSV with the supplied column headers. This is a
read-only catalogue query. Add an optional `indexes_csv` path to that dataset's
configuration. Paths are relative to the configuration file. Keep local catalogue
exports and custom configurations outside the shared dataset folders, for example
under the ignored `inputs/` directory. A missing inventory is shown as unavailable,
not interpreted as a database with no indexes. Potential coverage checks equality
keys, range-key order and included columns; filtered and non-rowstore indexes are
left for manual review. Implicit clustered-key coverage is not inferred.

The **Baseline / indexed** page accepts optional `indexed_plans`,
`indexed_summary` and `indexed_indexes_csv` paths alongside the baseline inputs:

```json
{
  "1GB": {
    "plans": "baseline/plans.zip",
    "summary": "baseline/timings.csv",
    "indexes_csv": "baseline/indexes.csv",
    "indexed_plans": "indexed/plans.zip",
    "indexed_summary": "indexed/timings.csv",
    "indexed_indexes_csv": "indexed/indexes.csv"
  }
}
```

Rebuild with `python -B -m tools.query_analyzer build --config path/to/config.json`.
Plans may be ZIP archives or directories of XML/sqlplan files. The comparison
shows SQL matching, structural changes, operator count changes, both plan trees
and paired average durations when supplied. Neither captures nor timings are
generated by the website. Matching SQL does not establish identical statistics,
server settings, cache conditions or results; validate those in the experiment.
The bundled configuration has no indexed inputs and displays explicit pending
states. The original cross-size comparison remains available separately.

- Optimizer costs are not milliseconds. Scans and hashes alone do not prove a fault.
- MissingIndex entries are hypotheses, not verified index improvements. Duplicate
  column requests are grouped; compare against existing indexes before testing.
- Runtime counters are preserved. Cardinality mismatch is not inferred for
  operators with multiple active workers, where serial division can mislead.
- Structural matching excludes node IDs, estimates/costs and database names; it
  preserves operation types, branch order and index access. Captured SQL is checked
  separately. Equal structure does not imply equal performance.
- Historical timings join by filename, not a verified SQL fingerprint. Check SQL,
  data, indexes, statistics and server settings before treating differences as a
  consequence of size alone.
- New plan exports can include SQL literals: review them before committing.
