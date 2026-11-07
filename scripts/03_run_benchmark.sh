#!/bin/bash
set -euo pipefail

if [[ $# -lt 2 || -z "${1:-}" || -z "${2:-}" ]]; then
    echo "Usage: $0 <SCALE_FOLDER> <DB_NAME> [--optimized] [--full-stats-after-warmup]" >&2
    exit 1
fi

SCALE_FOLDER=$1
DB_NAME=$2
shift 2

if [[ ! "$SCALE_FOLDER" =~ ^(1|2|5|10)GB$ ]]; then
    echo "Error: unsupported scale folder: $SCALE_FOLDER" >&2
    exit 1
fi
if [[ ! "$DB_NAME" =~ ^[A-Za-z0-9_]+$ ]]; then
    echo "Error: unsupported database name: $DB_NAME" >&2
    exit 1
fi

OPTIMIZED_QUERYSET=false
FULL_STATS_AFTER_WARMUP=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --optimized) OPTIMIZED_QUERYSET=true ;;
        --full-stats-after-warmup) FULL_STATS_AFTER_WARMUP=true ;;
        *) echo "Unknown parameter: $1" >&2; exit 1 ;;
    esac
    shift
done

WARMUP_RUNS=${WARMUP_RUNS:-1}
MEASURED_RUNS=${MEASURED_RUNS:-5}
if [[ ! "$WARMUP_RUNS" =~ ^[0-9]+$ || ! "$MEASURED_RUNS" =~ ^[1-9][0-9]*$ ]]; then
    echo "Error: WARMUP_RUNS must be >= 0 and MEASURED_RUNS must be >= 1." >&2
    exit 1
fi
WARMUP_RUNS=$((10#$WARMUP_RUNS))
MEASURED_RUNS=$((10#$MEASURED_RUNS))
TOTAL_RUNS=$((WARMUP_RUNS + MEASURED_RUNS))

QUERYSET_NAME=queryset
if [ "$OPTIMIZED_QUERYSET" = true ]; then
    QUERYSET_NAME=queryset-optimized
fi
if [ -n "${QUERY_DIR_OVERRIDE:-}" ]; then
    case "$QUERY_DIR_OVERRIDE" in
        /benchmark_results/optimization_exploration/querysets/*) ;;
        *) echo "Error: QUERY_DIR_OVERRIDE is outside the exploration queryset area." >&2; exit 1 ;;
    esac
    QUERYSET_NAME=$(basename "$QUERY_DIR_OVERRIDE")
    QUERY_FILE="$QUERY_DIR_OVERRIDE/benchmark.sql"
else
    QUERY_FILE="/data/${SCALE_FOLDER}/${QUERYSET_NAME}/benchmark.sql"
fi
if [ ! -f "$QUERY_FILE" ]; then
    echo "Error: benchmark file not found: $QUERY_FILE" >&2
    exit 1
fi

SQLCMD_BIN=${SQLCMD_BIN:-/opt/mssql-tools18/bin/sqlcmd}
SQLCMD_QUERY_TIMEOUT_SECONDS=${SQLCMD_QUERY_TIMEOUT_SECONDS:-0}
if [[ ! "$SQLCMD_QUERY_TIMEOUT_SECONDS" =~ ^[0-9]+$ ]]; then
    echo "Error: SQLCMD_QUERY_TIMEOUT_SECONDS must be a non-negative integer." >&2
    exit 1
fi
SA_PASS=${MSSQL_SA_PASSWORD:?MSSQL_SA_PASSWORD is required}
RESULTS_DIR=${RESULTS_DIR:-/benchmark_results}
RUN_ID=$(date -u +%Y%m%dT%H%M%SZ)
RUN_DIR="$RESULTS_DIR/whole_suite_${DB_NAME}_${SCALE_FOLDER}_${QUERYSET_NAME}_${RUN_ID}"
mkdir -p "$RUN_DIR"
TIMINGS_CSV="$RUN_DIR/timings.csv"
printf 'phase,run,duration_ms\n' > "$TIMINGS_CSV"

echo "=================================================="
echo "Starting TPC-DS benchmark against database: $DB_NAME"
echo "Queryset: $QUERYSET_NAME ($QUERY_FILE)"
echo "Protocol: $TOTAL_RUNS total runs ($WARMUP_RUNS warm-up, $MEASURED_RUNS measured)"
echo "Results: $RUN_DIR"
echo "=================================================="

TOTAL_SUM_MS=0
for ((RUN=1; RUN<=TOTAL_RUNS; RUN++)); do
    if ((RUN <= WARMUP_RUNS)); then
        PHASE=warmup
        PHASE_RUN=$RUN
    else
        PHASE=measured
        PHASE_RUN=$((RUN - WARMUP_RUNS))
    fi
    echo "Starting $PHASE run $PHASE_RUN..."
    START_TIME=$(date +%s%3N)
    SQLCMD_OPTIONS=(-b -S localhost -U sa -P "$SA_PASS" -C -d "$DB_NAME")
    if ((SQLCMD_QUERY_TIMEOUT_SECONDS > 0)); then
        SQLCMD_OPTIONS+=(-t "$SQLCMD_QUERY_TIMEOUT_SECONDS")
    fi
    "$SQLCMD_BIN" "${SQLCMD_OPTIONS[@]}" -i "$QUERY_FILE" -o /dev/null
    END_TIME=$(date +%s%3N)
    DURATION_MS=$((END_TIME - START_TIME))
    printf '%s,%d,%d\n' "$PHASE" "$PHASE_RUN" "$DURATION_MS" >> "$TIMINGS_CSV"
    echo "  -> $PHASE run $PHASE_RUN completed in ${DURATION_MS}ms."

    if [ "$PHASE" = warmup ] && ((RUN == WARMUP_RUNS)) && \
       [ "$FULL_STATS_AFTER_WARMUP" = true ]; then
        echo "Updating statistics with FULLSCAN after the final warm-up..."
        "$SQLCMD_BIN" -b -S localhost -U sa -P "$SA_PASS" -C \
            -d "$DB_NAME" -i /scripts/full_stats.sql
        echo "Clearing the database-scoped procedure cache..."
        "$SQLCMD_BIN" -b -S localhost -U sa -P "$SA_PASS" -C \
            -d "$DB_NAME" -i /scripts/clear_stats_cache.sql
    fi

    if [ "$PHASE" = measured ]; then
        TOTAL_SUM_MS=$((TOTAL_SUM_MS + DURATION_MS))
    fi
done

AVERAGE_MS=$((TOTAL_SUM_MS / MEASURED_RUNS))
AVERAGE_SEC=$(awk -v value="$AVERAGE_MS" 'BEGIN {printf "%.3f", value/1000}')

echo "=================================================="
echo "Benchmark completed for $DB_NAME!"
echo "Total time for $MEASURED_RUNS measured runs : ${TOTAL_SUM_MS}ms"
echo "Average Execution Time         : ${AVERAGE_MS}ms (${AVERAGE_SEC}s)"
echo "Timing CSV                     : $TIMINGS_CSV"
echo "=================================================="
