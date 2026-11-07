#!/bin/bash
set -euo pipefail

DB_NAME=${1:-}
shift 1

SQLCMD_BIN=${SQLCMD_BIN:-/opt/mssql-tools18/bin/sqlcmd}
SA_PASS=${MSSQL_SA_PASSWORD:-}
INDEXING_FILE=${INDEXING_FILE:-/scripts/indexing_strategy.sql}

REFRESH_STATS=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --refresh-stats)
            REFRESH_STATS=true
            ;;
        *)
            echo "Unknown parameter: $1" >&2
            exit 1
            ;;
    esac
    shift
done

if [ -z "$DB_NAME" ]; then
    echo "Error: Usage: $0 <DB_NAME>" >&2
    exit 1
fi

if [[ ! "$DB_NAME" =~ ^[A-Za-z0-9_]+$ ]]; then
    echo "Error: Database name contains unsupported characters: $DB_NAME" >&2
    exit 1
fi

if [ -z "$SA_PASS" ]; then
    echo "Error: MSSQL_SA_PASSWORD is not set." >&2
    exit 1
fi

if [ ! -x "$SQLCMD_BIN" ]; then
    echo "Error: sqlcmd was not found or is not executable: $SQLCMD_BIN" >&2
    exit 1
fi

if [ ! -f "$INDEXING_FILE" ]; then
    echo "Error: Indexing strategy was not found: $INDEXING_FILE" >&2
    exit 1
fi

echo "Applying indexing strategy to database: $DB_NAME"
"$SQLCMD_BIN" -b -S localhost -U sa -P "$SA_PASS" -C \
    -d "$DB_NAME" -i "$INDEXING_FILE"
echo "Indexing strategy applied to $DB_NAME."

if [ "$REFRESH_STATS" = true ]; then
    "$SQLCMD_BIN" -b -S localhost -U sa -P "$SA_PASS" -C \
        -d "$DB_NAME" -i /scripts/full_stats.sql
    "$SQLCMD_BIN" -b -S localhost -U sa -P "$SA_PASS" -C \
        -d "$DB_NAME" -i /scripts/clear_stats_cache.sql
    echo "Statistics update completed."
fi