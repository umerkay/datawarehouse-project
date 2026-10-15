#!/bin/bash
set -e

SCALE_FOLDER=$1
DB_NAME=$2

if [ -z "$DB_NAME" ]; then
    echo "Error: Missing database name."
    exit 1
fi

QUERY_DIR="/data/${SCALE_FOLDER}/queryset"
SA_PASS=$MSSQL_SA_PASSWORD

if [ -z "$(ls -A $QUERY_DIR/*.sql 2>/dev/null)" ]; then
    echo "Error: No .sql files found in $QUERY_DIR."
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

    for query_file in "$QUERY_DIR"/*.sql; do
        /opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -P "$SA_PASS" -C -d $DB_NAME -i "$query_file" -o /dev/null
    done

    # Record end time in milliseconds
    END_TIME=$(date +%s%3N)
    DURATION_MS=$((END_TIME - START_TIME))

    if [ $RUN -eq 1 ]; then
        echo "  -> Warm-up completed in ${DURATION_MS}ms."
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