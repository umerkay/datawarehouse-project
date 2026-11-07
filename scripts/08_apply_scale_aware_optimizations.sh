#!/bin/bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
    echo "Usage: $0 <1GB|2GB|5GB|10GB> <database>" >&2
    exit 2
fi
SCALE=$1
DATABASE=$2
[[ "$SCALE" =~ ^(1|2|5|10)GB$ ]] || { echo "Unsupported scale: $SCALE" >&2; exit 2; }
[[ "$DATABASE" =~ ^[A-Za-z0-9_]+$ ]] || { echo "Unsupported database name: $DATABASE" >&2; exit 2; }

SQLCMD=${SQLCMD:-/opt/mssql-tools18/bin/sqlcmd}
SA_PASS=${MSSQL_SA_PASSWORD:?MSSQL_SA_PASSWORD is required}

echo "Refreshing workload statistics with FULLSCAN in $DATABASE..."
"$SQLCMD" -b -S localhost -U sa -P "$SA_PASS" -C -d "$DATABASE" \
    -i /scripts/full_stats.sql

case "$SCALE" in
    5GB|10GB)
        echo "Applying validated large-scale dimension indexes..."
        "$SQLCMD" -b -S localhost -U sa -P "$SA_PASS" -C -d "$DATABASE" \
            -i /scripts/scale_aware_dimension_indexes.sql
        ;;
    1GB|2GB)
        echo "Skipping helper indexes: they regress the tested small-scale workload."
        ;;
esac

"$SQLCMD" -b -S localhost -U sa -P "$SA_PASS" -C -d "$DATABASE" \
    -i /scripts/clear_stats_cache.sql
echo "Scale-aware optimizations applied to $DATABASE."
