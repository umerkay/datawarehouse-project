#!/bin/bash
set -e

if [[ $# -lt 2 ]]; then
    echo "Usage: $0 <SCALE_FOLDER> <DB_NAME> [--full-stats-after-warmup]" >&2
    exit 1
fi

SCALE_FOLDER=$1
DB_NAME=$2
shift 2

if [ -z "$DB_NAME" ]; then
    echo "Error: Missing database name."
    exit 1
fi

FULL_STATS_AFTER_WARMUP=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --full-stats-after-warmup)
            FULL_STATS_AFTER_WARMUP=true
            ;;
        *)
            echo "Unknown parameter: $1" >&2
            exit 1
            ;;
    esac
    shift
done
QUERY_DIR="/data/${SCALE_FOLDER}/queryset"
QUERY_FILE="$QUERY_DIR/benchmark.sql"
SA_PASS=$MSSQL_SA_PASSWORD

if [ ! -f "$QUERY_FILE" ]; then
    echo "Error: Benchmark file not found: $QUERY_FILE."
    exit 1
fi

echo "=================================================="
echo "Starting TPC-DS benchmark against database: $DB_NAME"
echo "Protocol: 6 total runs (1 warm-up, 5 measured)"
echo "=================================================="

TOTAL_SUM_MS=0

for RUN in {1..6}; do
    if [ $RUN -eq 1 ]; then
        echo "Starting Run 1 (Warm-up - Time will be discarded)..."
    else
        echo "Starting Run $RUN (Measured)..."
    fi

    # Record time in milliseconds
    START_TIME=$(date +%s%3N)

    /opt/mssql-tools18/bin/sqlcmd -b -S localhost -U sa -P "$SA_PASS" -C -d $DB_NAME -i "$QUERY_FILE" -o /dev/null

    # Record end time in milliseconds
    END_TIME=$(date +%s%3N)
    DURATION_MS=$((END_TIME - START_TIME))

    if [ $RUN -eq 1 ]; then
        echo "  -> Warm-up completed in ${DURATION_MS}ms."
        if [ "$FULL_STATS_AFTER_WARMUP" = true ]; then
            echo "Updating statistics with FULLSCAN before measured runs..."
            /opt/mssql-tools18/bin/sqlcmd -b -S localhost -U sa -P "$SA_PASS" -C \
                -d "$DB_NAME" -i /scripts/full_stats.sql
            echo "Statistics update completed; measured runs will begin."
        fi
    else
        echo "  -> Run $RUN completed in ${DURATION_MS}ms."
        TOTAL_SUM_MS=$((TOTAL_SUM_MS + DURATION_MS))
    fi
done

# Calculate the average of the 5 measured runs
AVERAGE_MS=$((TOTAL_SUM_MS / 5))

# Convert the final average to seconds for readability (using awk for floating point math)
AVERAGE_SEC=$(awk "BEGIN {print $AVERAGE_MS/1000}")

echo "=================================================="
echo "Benchmark completed for $DB_NAME!"
echo "Total time for 5 measured runs : ${TOTAL_SUM_MS}ms"
echo "Average Execution Time         : ${AVERAGE_MS}ms (${AVERAGE_SEC}s)"
echo "=================================================="