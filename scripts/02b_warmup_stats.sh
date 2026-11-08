#!/bin/bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
    echo "Usage: $0 <SCALE_FOLDER> <DB_NAME> [--optimized]" >&2
    exit 1
fi

SCALE_FOLDER=$1
DB_NAME=$2
shift 2
OPTIMIZED_QUERYSET=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --optimized)
            OPTIMIZED_QUERYSET=true
            ;;
        *)
            echo "Unknown parameter: $1" >&2
            exit 1
            ;;
    esac
    shift
done

QUERY_DIR="/data/${SCALE_FOLDER}/queryset"
if [ "$OPTIMIZED_QUERYSET" = true ]; then
    QUERY_DIR="/data/${SCALE_FOLDER}/queryset-optimized"
fi
QUERY_FILE="$QUERY_DIR/benchmark.sql"
SQLCMD_BIN=/opt/mssql-tools18/bin/sqlcmd
SA_PASS=${MSSQL_SA_PASSWORD:-}

if [[ ! "$SCALE_FOLDER" =~ ^[1-9][0-9]*GB$ ]]; then
    echo "Error: Scale folder must look like 1GB or 10GB (received: $SCALE_FOLDER)." >&2
    exit 1
fi

if [[ ! "$DB_NAME" =~ ^[A-Za-z0-9_]+$ ]]; then
    echo "Error: Invalid database name: $DB_NAME" >&2
    exit 1
fi

if [[ -z "$SA_PASS" ]]; then
    echo "Error: MSSQL_SA_PASSWORD is not set." >&2
    exit 1
fi

if [[ ! -s "$QUERY_FILE" ]]; then
    echo "Error: Benchmark file is missing or empty: $QUERY_FILE" >&2
    exit 1
fi

echo "Running one warm-up of the full workload against $DB_NAME..."
START_MS=$(date +%s%3N)
"$SQLCMD_BIN" -b -I -r1 -S localhost -U sa -P "$SA_PASS" -C \
    -d "$DB_NAME" -i "$QUERY_FILE" -o /dev/null
END_MS=$(date +%s%3N)
echo "Warm-up completed in $((END_MS - START_MS))ms."

echo "Updating statistics with FULLSCAN..."
"$SQLCMD_BIN" -b -S localhost -U sa -P "$SA_PASS" -C \
    -d "$DB_NAME" -i /scripts/full_stats.sql

echo "Clearing the database-scoped procedure cache..."
"$SQLCMD_BIN" -b -S localhost -U sa -P "$SA_PASS" -C \
    -d "$DB_NAME" -i /scripts/clear_stats_cache.sql

echo "Warm-up, statistics update, and procedure-cache clear completed for $DB_NAME."
