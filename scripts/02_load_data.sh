#!/bin/bash
set -e

SCALE_FOLDER=$1
DB_NAME=$2

if [ -z "$SCALE_FOLDER" ] || [ -z "$DB_NAME" ]; then
    echo "Error: Missing arguments."
    exit 1
fi

echo "Starting data load for scale: $SCALE_FOLDER into database: $DB_NAME"

# Store password separately to prevent Bash quoting bugs
SA_PASS=$MSSQL_SA_PASSWORD

echo "Creating database $DB_NAME..."
/opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -P "$SA_PASS" -C -Q "DROP DATABASE IF EXISTS $DB_NAME; CREATE DATABASE $DB_NAME;"

echo "Creating schema..."
/opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -P "$SA_PASS" -C -d $DB_NAME -i /tpcds-kit/tools/tpcds.sql
/opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -P "$SA_PASS" -C -d $DB_NAME -i /tpcds-kit/tools/tpcds_source.sql

DATA_DIR="/tpcds-kit/data_${SCALE_FOLDER}"

if [ ! -d "$DATA_DIR" ]; then
    echo "Error: Directory $DATA_DIR does not exist."
    exit 1
fi

echo "Beginning bulk inserts..."
for file in $DATA_DIR/*.dat; do
    tablename=$(basename "$file" .dat)
    echo "Loading $tablename..."
    /opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -P "$SA_PASS" -C -d $DB_NAME -Q "BULK INSERT $tablename FROM '$file' WITH (FIELDTERMINATOR = '|', ROWTERMINATOR = '|\n', TABLOCK);"
done

echo "Setting up table constraints and foreign key referential integrity..."
/opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -P "$SA_PASS" -C -d $DB_NAME -i /tpcds-kit/tools/tpcds_ri.sql

echo "Successfully loaded all $SCALE_FOLDER data into $DB_NAME!"