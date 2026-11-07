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
echo "Creating the six selected dimension indexes..."
"$SQLCMD_BIN" -b -S localhost -U sa -P "$SA_PASS" -C \
    -d "$DB_NAME" <<'SQL'
SET NOCOUNT ON;
SET XACT_ABORT ON;

-- Selected from the dimension-index experiment: single-column B-tree indexes.
-- Keep the existing fact CCIs, primary keys and materialized-view strategy.
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID(N'dbo.customer') AND name = N'IX_customer_current_addr')
    CREATE NONCLUSTERED INDEX IX_customer_current_addr ON dbo.customer(c_current_addr_sk);

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID(N'dbo.customer_address') AND name = N'IX_customer_address_state')
    CREATE NONCLUSTERED INDEX IX_customer_address_state ON dbo.customer_address(ca_state);

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID(N'dbo.date_dim') AND name = N'IX_date_dim_year')
    CREATE NONCLUSTERED INDEX IX_date_dim_year ON dbo.date_dim(d_year);

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID(N'dbo.date_dim') AND name = N'IX_date_dim_moy')
    CREATE NONCLUSTERED INDEX IX_date_dim_moy ON dbo.date_dim(d_moy);

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID(N'dbo.date_dim') AND name = N'IX_date_dim_date')
    CREATE NONCLUSTERED INDEX IX_date_dim_date ON dbo.date_dim(d_date);

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID(N'dbo.date_dim') AND name = N'IX_date_dim_month_seq')
    CREATE NONCLUSTERED INDEX IX_date_dim_month_seq ON dbo.date_dim(d_month_seq);
GO
SQL

echo "Applying materialized views from: $INDEXING_FILE"
"$SQLCMD_BIN" -b -S localhost -U sa -P "$SA_PASS" -C \
    -d "$DB_NAME" -i "$INDEXING_FILE"
echo "Indexing strategy applied to $DB_NAME."
