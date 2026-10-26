#!/bin/bash
set -e

SCALE_FOLDER=$1
DB_NAME=$2
shift 2

TPCDS_BASE_DIR="tpcds-kit"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --optimized)
            TPCDS_BASE_DIR="tpcds-optimized"
            ;;
        *)
            echo "Unknown parameter: $1" >&2
            exit 1
            ;;
    esac
    shift
done

if [ -z "$SCALE_FOLDER" ] || [ -z "$DB_NAME" ]; then
    echo "Error: Missing arguments."
    exit 1
fi

echo "Starting data load for scale: $SCALE_FOLDER into database: $DB_NAME"

# Store password separately to prevent Bash quoting bugs
SA_PASS=$MSSQL_SA_PASSWORD

echo "Creating database $DB_NAME..."
/opt/mssql-tools18/bin/sqlcmd -b -S localhost -U sa -P "$SA_PASS" -C -Q "DROP DATABASE IF EXISTS $DB_NAME; CREATE DATABASE $DB_NAME;"

echo "Creating schema..."
/opt/mssql-tools18/bin/sqlcmd -b -S localhost -U sa -P "$SA_PASS" -C -d $DB_NAME -i "/$TPCDS_BASE_DIR/tools/tpcds.sql"
/opt/mssql-tools18/bin/sqlcmd -b -S localhost -U sa -P "$SA_PASS" -C -d $DB_NAME -i "/$TPCDS_BASE_DIR/tools/tpcds_source.sql"

DATA_DIR="/data/${SCALE_FOLDER}/dataset"

if [ ! -d "$DATA_DIR" ]; then
    echo "Error: Directory $DATA_DIR does not exist."
    exit 1
fi

echo "Beginning bulk inserts..."
shopt -s nullglob
data_files=("$DATA_DIR"/*.dat)
if [ "${#data_files[@]}" -eq 0 ]; then
    echo "Error: No .dat files found in $DATA_DIR."
    exit 1
fi

for file in "${data_files[@]}"; do
    filename=$(basename "$file")
    tablename=${filename%.dat}
    echo "Loading $tablename..."
    /opt/mssql-tools18/bin/sqlcmd -b -S localhost -U sa -P "$SA_PASS" -C -d "$DB_NAME" -Q "BULK INSERT $tablename FROM '$file' WITH (FIELDTERMINATOR = '|', ROWTERMINATOR = '|\n', TABLOCK);"
done

echo "Setting up table constraints and foreign key referential integrity..."
/opt/mssql-tools18/bin/sqlcmd -b -S localhost -U sa -P "$SA_PASS" -C -d $DB_NAME -i "/$TPCDS_BASE_DIR/tools/tpcds_ri.sql"

echo "Successfully loaded all $SCALE_FOLDER data into $DB_NAME!"