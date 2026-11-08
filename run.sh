#!/usr/bin/env bash
set -e

SCALE=${1:-1}
if [[ ! "$SCALE" =~ ^[1-9][0-9]*$ ]]; then
    echo "Usage: ./run.sh [positive scale factor in GB]" >&2
    exit 1
fi

ROOT_DIR=$(cd -- "$(dirname -- "$0")" && pwd)
cd "$ROOT_DIR"

if [[ ! -f .env && -z "${MSSQL_SA_PASSWORD:-}" ]]; then
    printf 'MSSQL_SA_PASSWORD="Aa1!%s"\n' "$(openssl rand -hex 24)" > .env
    chmod 600 .env
fi

SCALE_FOLDER="${SCALE}GB"
SCALE_ID=$(printf '%02d' "$SCALE")
BASELINE_DB="tpcds_${SCALE_ID}"
OPTIMIZED_DB="${BASELINE_DB}_optimized"

docker compose up -d --build
docker exec tpcds_workspace bash /scripts/01_generate_data.sh "$SCALE"

docker exec tpcds_sqlserver bash /scripts/02_load_data.sh \
    "$SCALE_FOLDER" "$BASELINE_DB"
docker exec tpcds_sqlserver bash /scripts/02_load_data.sh \
    "$SCALE_FOLDER" "$OPTIMIZED_DB" --optimized
docker exec tpcds_sqlserver bash /scripts/05_apply_indexing.sh "$OPTIMIZED_DB"

echo "Running baseline benchmark..."
BASELINE_OUTPUT=$(docker exec tpcds_sqlserver \
    bash /scripts/03_run_benchmark.sh "$SCALE_FOLDER" "$BASELINE_DB")
printf '%s\n' "$BASELINE_OUTPUT"

echo "Running optimized benchmark..."
docker exec tpcds_sqlserver bash /scripts/02b_warmup_stats.sh \
    "$SCALE_FOLDER" "$OPTIMIZED_DB" --optimized
OPTIMIZED_OUTPUT=$(docker exec tpcds_sqlserver \
    bash /scripts/03_run_benchmark.sh "$SCALE_FOLDER" "$OPTIMIZED_DB" --optimized)
printf '%s\n' "$OPTIMIZED_OUTPUT"

BASELINE_TOTAL=$(printf '%s\n' "$BASELINE_OUTPUT" | awk -F': ' '/Total time for 5 measured runs/{sub(/ms.*/, "", $2); print $2}')
BASELINE_AVERAGE=$(printf '%s\n' "$BASELINE_OUTPUT" | awk -F': ' '/Average Execution Time/{sub(/ms.*/, "", $2); print $2}')
OPTIMIZED_TOTAL=$(printf '%s\n' "$OPTIMIZED_OUTPUT" | awk -F': ' '/Total time for 5 measured runs/{sub(/ms.*/, "", $2); print $2}')
OPTIMIZED_AVERAGE=$(printf '%s\n' "$OPTIMIZED_OUTPUT" | awk -F': ' '/Average Execution Time/{sub(/ms.*/, "", $2); print $2}')
SPEEDUP=$(awk -v base="$BASELINE_AVERAGE" -v opt="$OPTIMIZED_AVERAGE" \
    'BEGIN { if (opt == 0) print "N/A"; else printf "%.2fx", base / opt }')

mkdir -p benchmark_results
SUMMARY="benchmark_results/summary_${SCALE_FOLDER}.csv"
{
    echo "scale,baseline_database,baseline_total_ms,baseline_average_ms,optimized_database,optimized_total_ms,optimized_average_ms,speedup"
    echo "$SCALE_FOLDER,$BASELINE_DB,$BASELINE_TOTAL,$BASELINE_AVERAGE,$OPTIMIZED_DB,$OPTIMIZED_TOTAL,$OPTIMIZED_AVERAGE,$SPEEDUP"
} > "$SUMMARY"

echo
echo "Summary ($SCALE_FOLDER)"
printf 'Baseline average:   %s ms (total %s ms)\n' "$BASELINE_AVERAGE" "$BASELINE_TOTAL"
printf 'Optimized average:  %s ms (total %s ms)\n' "$OPTIMIZED_AVERAGE" "$OPTIMIZED_TOTAL"
echo "Speedup: $SPEEDUP"
echo "Saved to $SUMMARY"
