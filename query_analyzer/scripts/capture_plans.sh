#!/usr/bin/env bash
# Read-only estimated-plan capture from an already populated SQL Server database.
set -euo pipefail
if [[ $# -lt 2 ]]; then
    echo "Usage: $0 <1GB|2GB|5GB|10GB> <DATABASE> [QUERY_INDEX ...]" >&2
    exit 1
fi
scale=$1
database=$2
shift 2
case "$scale" in 1GB|2GB|5GB|10GB) ;; *) echo "Unsupported size: $scale" >&2; exit 1;; esac
[[ "$database" =~ ^[A-Za-z0-9_]+$ ]] || { echo "Invalid database name" >&2; exit 1; }
tool_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
query_dir=${QUERY_DIR:-/data/$scale/queryset}
output_dir=${OUTPUT_DIR:-$tool_root/datasets/$scale/plans}
sqlcmd_bin=${SQLCMD_BIN:-/opt/mssql-tools18/bin/sqlcmd}
server=${SQL_SERVER:-localhost}
user=${SQL_USER:-sa}
export SQLCMDPASSWORD=${SQLCMDPASSWORD:-${MSSQL_SA_PASSWORD:-}}
[[ -n "$SQLCMDPASSWORD" ]] || { echo "Set SQLCMDPASSWORD (or MSSQL_SA_PASSWORD)" >&2; exit 1; }
[[ -x "$sqlcmd_bin" ]] || { echo "Set SQLCMD_BIN to your sqlcmd executable" >&2; exit 1; }
[[ -f "$query_dir/queries.lst" ]] || { echo "Missing queries.lst in $query_dir" >&2; exit 1; }
mapfile -t files < "$query_dir/queries.lst"
[[ ${#files[@]} -gt 0 ]] || { echo "Query list is empty" >&2; exit 1; }
selected=("$@")
if [[ ${#selected[@]} -eq 0 ]]; then
    for ((i=1;i<=${#files[@]};i++)); do selected+=("$i"); done
fi
for i in "${selected[@]}"; do
    [[ "$i" =~ ^[1-9][0-9]{0,8}$ ]] && ((i<=${#files[@]})) || { echo "Invalid query index: $i" >&2; exit 1; }
    file=${files[i-1]%$'\r'}
    [[ "$file" =~ ^[A-Za-z0-9_.-]+\.sql$ && -f "$query_dir/$file" ]] || { echo "Missing or invalid query file: $file" >&2; exit 1; }
done
mkdir -p "$output_dir"
wrapper=$(mktemp)
trap 'rm -f -- "$wrapper"' EXIT
for i in "${selected[@]}"; do
    file=${files[i-1]%$'\r'}
    target="$output_dir/${file%.sql}.xml"
    printf 'SET SHOWPLAN_XML ON;\nGO\n:r "%s"\nGO\nSET SHOWPLAN_XML OFF;\nGO\n' "$query_dir/$file" > "$wrapper"
    echo "$scale: estimated plan $i/${#files[@]} $file"
    if "$sqlcmd_bin" -b -S "$server" -U "$user" -C -d "$database" -t "${PLAN_TIMEOUT:-120}" -y 0 -w 65535 -i "$wrapper" -o "$target.tmp"; then
        mv -- "$target.tmp" "$target"
    else
        mv -- "$target.tmp" "$target.failed.txt"
        echo "Capture failed: $file. Queries have not been benchmarked." >&2
        exit 1
    fi
done
echo "Estimated plans captured in $output_dir. Run: python -m query_analyzer build"
