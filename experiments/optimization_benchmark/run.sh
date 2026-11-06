#!/usr/bin/env bash
set -euo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
root=$(cd -- "$here/../.." && pwd)
usage() {
    echo 'Full experiment: bash experiments/optimization_benchmark/run.sh --databases DB_1GB DB_2GB DB_5GB [--query-scale 1GB] [--service sqlserver] [--dry-run]'
    echo 'Usage: bash experiments/optimization_benchmark/run.sh --database SOURCE_DB [--scale 1GB|2GB|5GB|10GB] [--query-scale 1GB|2GB|5GB|10GB] [--service sqlserver] [--resume RAW_RUN_FOLDER] [--dry-run]'
}
die() { echo "Error: $*" >&2; exit 1; }
scale=1GB; query_scale=1GB; database=''; service=sqlserver; resume=''; dry_run=0
databases=(); scale_set=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --databases)
            [[ $# -ge 4 && $2 != --* && $3 != --* && $4 != --* ]] || die '--databases requires three database names in 1GB, 2GB, 5GB order'
            [[ ${#databases[@]} -eq 0 ]] || die 'Specify --databases only once'
            databases=("$2" "$3" "$4"); shift 4;;
        --scale|--query-scale|--database|--service|--resume)
            [[ $# -ge 2 && $2 != --* ]] || die "$1 requires a value"
            case "$1" in
                --scale) scale=$2; scale_set=1;; --query-scale) query_scale=$2;; --database) database=$2;;
                --service) service=$2;; --resume) resume=$2;;
            esac
            shift 2;;
        --dry-run) dry_run=1; shift;;
        --help|-h) usage; exit 0;;
        *) die "Unknown argument: $1";;
    esac
done
if [[ ${#databases[@]} -gt 0 ]]; then
    [[ -z $database && -z $resume && $scale_set == 0 ]] || die '--databases cannot be combined with --database, --scale or --resume'
    for name in "${databases[@]}"; do
        [[ $name =~ ^[A-Za-z0-9_]{1,70}$ ]] || die 'Invalid database name'
    done
    [[ ${databases[0]} != "${databases[1]}" && ${databases[0]} != "${databases[2]}" && ${databases[1]} != "${databases[2]}" ]] || die 'Use a different source database for each scale'
    scales=(1GB 2GB 5GB)
    for i in 0 1 2; do
        printf '\nSales index experiment: %s (%s)\n' "${scales[i]}" "${databases[i]}"
        command=(bash "$here/run.sh" --scale "${scales[i]}" --database "${databases[i]}" \
            --query-scale "$query_scale" --service "$service")
        if ((dry_run)); then command+=(--dry-run); fi
        "${command[@]}"
    done
    exit 0
fi
[[ $database =~ ^[A-Za-z0-9_]{1,70}$ ]] || die 'A baseline database name of 1-70 letters, digits or underscores is required'
case "$scale" in 1GB|2GB|5GB|10GB) ;; *) die 'Unsupported scale';; esac
case "$query_scale" in 1GB|2GB|5GB|10GB) ;; *) die 'Unsupported query scale';; esac
command -v docker >/dev/null || die 'Docker CLI not found'
[[ -f "$root/docker-compose.yml" ]] || die 'Keep this folder inside the project repository'
[[ -f "$root/tpcds-optimized/tools/tpcds.sql" ]] || die 'Missing the main optimized schema: tpcds-optimized/tools/tpcds.sql'
[[ -f "$root/data/$query_scale/queryset/queries.lst" ]] || die "Missing data/$query_scale/queryset/queries.lst"
if command -v python3 >/dev/null; then bootstrap=python3; else bootstrap=python; fi
if [[ ! -d "$here/.venv" ]]; then "$bootstrap" -m venv "$here/.venv"; fi
if [[ -x "$here/.venv/bin/python" ]]; then python_bin=$here/.venv/bin/python
else python_bin=$here/.venv/Scripts/python.exe; fi
if ((!dry_run)); then
    "$python_bin" -m pip install "matplotlib==3.9.4" "numpy==2.0.2"
    "$python_bin" -c 'import matplotlib, numpy'
fi

# Disable Git Bash conversion of container paths only for Docker commands.
docker_cmd() { MSYS_NO_PATHCONV=1 docker "$@"; }
host_path() { if command -v cygpath >/dev/null; then cygpath -w "$1"; else printf '%s\n' "$1"; fi; }
cd -- "$root"
container=$(docker_cmd compose ps -q "$service" | tr -d '\r')
[[ -n $container && $container != *$'\n'* ]] || die 'Exactly one running SQL Server service is required'
token=$("$python_bin" -c 'from datetime import datetime, timezone; import uuid; print(datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"_"+uuid.uuid4().hex[:8])')
remote=/tmp/optimization_$token
results=$root/benchmark_results
mkdir -p "$results"
results=$(cd -- "$results" && pwd)
scenarios=(baseline date_keys date_shared)
if [[ -n $resume ]]; then
    [[ -d $resume && -f $resume/manifest.txt ]] || die '--resume must point to an existing raw run'
    resume=$(cd -- "$resume" && pwd)
    [[ $resume == "$results/"* ]] || die '--resume must be inside benchmark_results'
    "$python_bin" - "$resume/manifest.txt" "$here" <<'PY'
import hashlib, pathlib, sys
manifest = dict(line.split('=', 1) for line in pathlib.Path(sys.argv[1]).read_text().splitlines() if '=' in line)
if manifest.get('protocol') != 'cci_main_v3':
    raise SystemExit('Old rowstore-base experiments cannot be resumed with this protocol')
for key, name in [('index_definition_sha256', 'indexes.sql'), ('schema_sha256', 'cci_schema.sql'), ('runner_sha256', 'benchmark.sh')]:
    here = pathlib.Path(sys.argv[2])
    source = here.parents[1]/'tpcds-optimized/tools/tpcds.sql' if name == 'cci_schema.sql' else here/name
    if manifest.get(key) != hashlib.sha256(source.read_bytes()).hexdigest():
        raise SystemExit(f'{name} differs from the interrupted run; start a fresh experiment')
PY
    parent=$(dirname -- "$resume")
    remote_results=/benchmark_results
else
    parent=$results/optimization_$token
    remote_results=/benchmark_results/optimization_$token
fi
# Copy only runtime files, never the virtual environment or generated outputs.
docker_cmd compose exec -T "$service" mkdir -p "$remote"
for file in benchmark.sh indexes.sql; do
    docker_cmd cp "$(host_path "$here/$file")" "$container:$remote/$file"
done
docker_cmd cp "$(host_path "$root/tpcds-optimized/tools/tpcds.sql")" "$container:$remote/cci_schema.sql"
command=(compose exec -T -e "RESULTS_DIR=$remote_results" -e "QUERY_DIR=/data/$query_scale/queryset" "$service" bash "$remote/benchmark.sh"
         "$scale" "$database" --scenarios "${scenarios[@]}")
if [[ -n $resume ]]; then
    command+=(--resume "/benchmark_results/${resume#"$results/"}")
fi
if ((dry_run)); then docker_cmd "${command[@]}" --dry-run; exit $?; fi
mkdir -p "$parent"
"$python_bin" - "$here" "$parent" "$token" "$scale" "$database" "$query_scale" "${scenarios[@]}" <<'PY'
import hashlib, json, pathlib, sys
import matplotlib, numpy
here, parent = map(pathlib.Path, sys.argv[1:3])
metadata = dict(timestamp_utc=sys.argv[3], scale=sys.argv[4], database=sys.argv[5],
                query_scale=sys.argv[6],
                protocol='cci_main_v3',
                scenarios=sys.argv[7:],
                python=sys.version, matplotlib=matplotlib.__version__, numpy=numpy.__version__,
                files={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in here.iterdir() if p.is_file()})
metadata['files']['tpcds-optimized/tools/tpcds.sql'] = hashlib.sha256((here.parents[1]/'tpcds-optimized/tools/tpcds.sql').read_bytes()).hexdigest()
(parent / ('invocation_' + sys.argv[3] + '.json')).write_text(json.dumps(metadata, indent=2), encoding='utf-8')
PY
set +e
docker_cmd "${command[@]}" 2>&1 | tee "$parent/execution_$token.log"
statuses=("${PIPESTATUS[@]}")
set -e
if ((statuses[0] != 0)); then
    echo "Experiment stopped (exit ${statuses[0]}). Raw data and logs retained in $parent" >&2
    exit "${statuses[0]}"
fi
((statuses[1] == 0)) || die 'Could not save the execution log'
if [[ -n $resume ]]; then run=$resume
else
    shopt -s nullglob
    manifests=("$parent"/findings_*/manifest.txt)
    [[ ${#manifests[@]} -eq 1 ]] || die "Expected exactly one results folder in $parent"
    run=$(dirname -- "${manifests[0]}")
fi
exec "$python_bin" "$here/analyze.py" "$run"
