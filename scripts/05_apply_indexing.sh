#!/bin/bash
set -euo pipefail

DB_NAME=${1:-}
SQLCMD_BIN=${SQLCMD_BIN:-/opt/mssql-tools18/bin/sqlcmd}
SA_PASS=${MSSQL_SA_PASSWORD:-}
INDEXING_FILE=${INDEXING_FILE:-/scripts/indexing_strategy.sql}

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
