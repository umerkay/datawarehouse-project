# TPC-DS optimization radar

An offline, static website for reviewing SQL Server execution plans and comparing
TPC-DS queries across 1GB, 2GB, 5GB and 10GB. The interface is in English.

## Open the website

From the repository root, with **Python 3.10 or newer**:

```bash
python -B -m query_analyzer build
python -m http.server 8000 --directory query_analyzer/site
```

Open **http://localhost:8000/**. Alternatively, open
`query_analyzer/site/index.html` directly or with VS Code Live Server.

The build uses only Python's standard library. No pip installation, SQL Server,
Docker, credentials, internet connection or raw `.dat` datasets are required to
view existing results. HTML contains the data before JavaScript runs; scripts
only enhance navigation, search and filtering. CSS/JS assets are local.

**Open `site/index.html`, not a file in `web/templates/`.**

In VS Code, you can also right-click **`query_analyzer/index.html`** and choose
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
as `query_015`, run `python -m query_analyzer pack 5GB` (or `10GB`), test, and
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
query_analyzer/
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

Run `python -B -m query_analyzer build` after editing. Never edit generated
`site/` files: the next build replaces them. Published asset URLs remain
`assets/...`; the build copies files from `web/assets/` into `site/assets/`.

`inputs/` and `local_tools/` are local-only archives, not application dependencies.
The earlier query-015 experiment and machine-specific troubleshooting scripts
remain in `local_tools/`; they are not part of the shared app. There is one CLI:
`python -m query_analyzer`, with `build` and `pack` subcommands. Earlier individual
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
python -B -m query_analyzer build --config path/to/sources.json --output path/to/site
python -B -m unittest discover -s query_analyzer/tests -v
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

Commit `query_analyzer/`, the two `query-analyzer-*.yml` workflows, `.gitignore`,
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
