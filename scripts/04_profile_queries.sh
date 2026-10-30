#!/bin/bash
set -euo pipefail

if [[ $# -lt 2 || -z "$1" || -z "$2" ]]; then
    echo "Usage: $0 <SCALE_FOLDER> <DB_NAME> [--from INDEX] [--to INDEX] [--list INDEX ...]" >&2
    exit 1
fi

# Required positional parameters
SCALE_FOLDER=$1
DB_NAME=${2:-}
shift 2

# Optional flags
_MAX_INT=2147483647
FROM=1
TO=$_MAX_INT
LIST=()
OPTIMIZED_QUERYSET=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --from)
            if [[ $# -lt 2 || ! "$2" =~ ^[1-9][0-9]{0,8}$ ]]; then
                echo "Error: --from requires a value greater than 0" >&2
                exit 1
            fi
            FROM="$2"
            shift 2
            ;;
        --to)
            if [[ $# -lt 2 || ! "$2" =~ ^[1-9][0-9]{0,8}$ ]]; then
                echo "Error: --to requires a value greater than 0" >&2
                exit 1
            fi
            TO="$2"
            shift 2
            ;;
        --list)
            shift
            if [[ $# -eq 0 || "$1" == --* ]]; then
                echo "Error: --list requires at least one positive integer index" >&2
                exit 1
            fi
            while [[ $# -gt 0 && "$1" != --* ]]; do
                if [[ ! "$1" =~ ^[1-9][0-9]{0,8}$ ]]; then
                    echo "Error: Invalid query index: $1" >&2
                    exit 1
                fi
                LIST+=("$1")
                shift
            done
            ;;
        --optimized)
            shift
            OPTIMIZED_QUERYSET=true
            ;;
        *)
            echo "Unknown parameter: $1" >&2
            exit 1
            ;;
    esac
done

if [ -z "$DB_NAME" ]; then
    echo "Error: Missing database name."
    exit 1
fi

# If list is provided, reset from/to to defaults
if [[ ${#LIST[@]} -gt 0 ]]; then
    FROM=1
    TO=$_MAX_INT
fi

if ((TO < FROM)); then
    echo "Invalid [--from .. --to] range, --from is greater than --to; nothing to run."
    exit 0
fi

QUERY_DIR="/data/$SCALE_FOLDER/queryset"
if [ "$OPTIMIZED_QUERYSET" = true ]; then
    QUERY_DIR="/data/$SCALE_FOLDER/queryset-optimized"
fi

RESULTS_DIR=${RESULTS_DIR:-/benchmark_results}
SQLCMD_BIN=${SQLCMD_BIN:-/opt/mssql-tools18/bin/sqlcmd}
SA_PASS=${MSSQL_SA_PASSWORD:-}
QUERY_LIST="$QUERY_DIR/queries.lst"
WARMUP_RUNS=${WARMUP_RUNS:-1}
MEASURED_RUNS=${MEASURED_RUNS:-5}

if [[ ! "$WARMUP_RUNS" =~ ^[0-9]+$ ]] || [[ ! "$MEASURED_RUNS" =~ ^[1-9][0-9]*$ ]]; then
    echo "Error: WARMUP_RUNS must be >= 0 and MEASURED_RUNS must be >= 1." >&2
    exit 1
fi

TOTAL_RUNS=$((WARMUP_RUNS + MEASURED_RUNS))

if [ ! -f "$QUERY_LIST" ]; then
    echo "Warning: Query list not found: $QUERY_LIST" >&2
    echo "Generate the split queries with:" >&2
    echo "docker exec -it tpcds_workspace bash /scripts/01_generate_data.sh --queryset --split-queries --no-recompile" >&2
    exit 1
fi

if [ -z "$SA_PASS" ]; then
    echo "Error: MSSQL_SA_PASSWORD is not set."
    exit 1
fi

if [ ! -x "$SQLCMD_BIN" ]; then
    echo "Error: sqlcmd was not found or is not executable: $SQLCMD_BIN"
    exit 1
fi

mapfile -t query_files < "$QUERY_LIST"
STATEMENT_COUNT=${#query_files[@]}
if [ "$STATEMENT_COUNT" -eq 0 ]; then
    echo "Error: Query list is empty: $QUERY_LIST" >&2
    exit 1
fi

for query_index in "${LIST[@]}"; do
    if ((query_index > STATEMENT_COUNT)); then
        echo "Error: Query index $query_index exceeds the number of queries ($STATEMENT_COUNT)." >&2
        exit 1
    fi
done

STATEMENTS=()
for source_file in "${query_files[@]}"; do
    if [[ ! "$source_file" =~ ^[A-Za-z0-9._-]+\.sql$ ]]; then
        echo "Error: Invalid SQL filename in $QUERY_LIST: $source_file" >&2
        exit 1
    fi

    query_file="$QUERY_DIR/$source_file"
    if [ ! -f "$query_file" ]; then
        echo "Error: SQL file listed in $QUERY_LIST was not found: $query_file" >&2
        exit 1
    fi

    STATEMENTS+=("${source_file%.sql}"$'\t'"$source_file"$'\t1\t'"$query_file")
done

mkdir -p "$RESULTS_DIR"
RUN_ID=$(date -u +%Y%m%dT%H%M%SZ)
RUN_RESULTS_DIR="$RESULTS_DIR/${DB_NAME}_${RUN_ID}"
mkdir -p "$RUN_RESULTS_DIR"
chmod 777 "$RUN_RESULTS_DIR"

RAW_CSV="$RUN_RESULTS_DIR/timings.csv"
SUMMARY_CSV="$RUN_RESULTS_DIR/summary.csv"
REPORT_HTML="$RUN_RESULTS_DIR/report.html"
printf 'run,query_id,source_file,statement_number,duration_ms\n' > "$RAW_CSV"

echo "=================================================="
echo "Starting TPC-DS per-query profile against database: $DB_NAME"
echo "Queries discovered: $STATEMENT_COUNT"
echo "Protocol: $TOTAL_RUNS total runs ($WARMUP_RUNS warm-up, $MEASURED_RUNS measured)"
echo "Results: $RUN_RESULTS_DIR"
echo "=================================================="

TOTAL_SUM_MS=0

for ((RUN = 1; RUN <= TOTAL_RUNS; RUN++)); do
    if [ "$RUN" -le "$WARMUP_RUNS" ]; then
        echo "Starting Run $RUN (Warm-up - individual times will be discarded)..."
    else
        echo "Starting Run $RUN (Measured)..."
    fi

    RUN_START_MS=$(date +%s%3N)

    if ((FROM > STATEMENT_COUNT)); then
        echo "The selected --from is at or beyond the number of queries; nothing to run."
        exit 0
    fi

    for ((STATEMENT_INDEX = FROM-1; STATEMENT_INDEX < STATEMENT_COUNT && STATEMENT_INDEX < TO; STATEMENT_INDEX++)); do
        QUERY_INDEX=$((STATEMENT_INDEX + 1))
        
        if [[ ${#LIST[@]} -gt 0 && " ${LIST[*]} " != *" $QUERY_INDEX "* ]]; then
            # QUERY_INDEX not in requested --list
            continue
        fi
        
        IFS=$'\t' read -r query_id source_file statement_number statement_file \
            <<< "${STATEMENTS[$STATEMENT_INDEX]}"
        printf '[Run %d/%d] Executing query %d/%d: %s (%s, statement %d)\n' \
            "$RUN" "$TOTAL_RUNS" "$QUERY_INDEX" "$STATEMENT_COUNT" "$query_id" "$source_file" "$statement_number"
        START_MS=$(date +%s%3N)

        if "$SQLCMD_BIN" -b -r1 -S localhost -U sa -P "$SA_PASS" -C -d "$DB_NAME" \
            -i "$statement_file" -o /dev/null; then
            END_MS=$(date +%s%3N)
            DURATION_MS=$((END_MS - START_MS))
            printf '[Run %d/%d] Completed %s in %dms\n' "$RUN" "$TOTAL_RUNS" "$query_id" "$DURATION_MS"
        else
            SQLCMD_STATUS=$?
            END_MS=$(date +%s%3N)
            DURATION_MS=$((END_MS - START_MS))
            printf >&2 '[Run %d/%d] FAILED %s (%s, statement %d) after %dms; sqlcmd exit code: %d\n' \
                "$RUN" "$TOTAL_RUNS" "$query_id" "$source_file" "$statement_number" "$DURATION_MS" "$SQLCMD_STATUS"
            echo "Contents of query file:"
            cat "$statement_file"
            exit "$SQLCMD_STATUS"
        fi

        if [ "$RUN" -gt "$WARMUP_RUNS" ]; then
            printf '%d,%s,%s,%d,%d\n' \
                "$((RUN - WARMUP_RUNS))" "$query_id" "$source_file" "$statement_number" "$DURATION_MS" \
                >> "$RAW_CSV"
        fi
    done

    RUN_END_MS=$(date +%s%3N)
    RUN_DURATION_MS=$((RUN_END_MS - RUN_START_MS))

    if [ "$RUN" -le "$WARMUP_RUNS" ]; then
        echo "  -> Warm-up completed in ${RUN_DURATION_MS}ms."
    else
        echo "  -> Run $RUN completed in ${RUN_DURATION_MS}ms."
        TOTAL_SUM_MS=$((TOTAL_SUM_MS + RUN_DURATION_MS))
    fi
done

{
    printf 'rank,query_id,source_file,statement_number,average_ms,min_ms,max_ms,total_ms,percent_of_total\n'
    awk -F, '
        NR == 1 { next }
        {
            key = $2
            source[key] = $3
            statement[key] = $4
            total[key] += $5
            count[key]++
            if (!(key in minimum) || $5 < minimum[key]) minimum[key] = $5
            if (!(key in maximum) || $5 > maximum[key]) maximum[key] = $5
        }
        END {
            for (key in total) {
                sum_average += total[key] / count[key]
            }
            for (key in total) {
                percentage = sum_average > 0 ? (total[key] / count[key]) / sum_average * 100 : 0
                printf "%s,%s,%s,%.1f,%d,%d,%d,%.2f\n",
                    key, source[key], statement[key], total[key] / count[key],
                    minimum[key], maximum[key], total[key], percentage
            }
        }
    ' "$RAW_CSV" |
        sort -t, -k4,4nr |
        awk -F, '{ printf "%d,%s\n", NR, $0 }'
} > "$SUMMARY_CSV"

MAX_AVERAGE_MS=$(awk -F, 'NR == 2 { print $5 }' "$SUMMARY_CSV")
awk -F, -v max_average="$MAX_AVERAGE_MS" -v database="$DB_NAME" -v run_id="$RUN_ID" -v measured_runs="$MEASURED_RUNS" '
    BEGIN {
        print "<!doctype html>"
        print "<html lang=\"en\"><head><meta charset=\"utf-8\">"
        print "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        print "<title>TPC-DS per-query benchmark</title>"
        print "<style>"
        print "body{font:14px system-ui,sans-serif;margin:2rem;color:#1f2937}"
        print "h1{margin-bottom:.25rem}.meta{color:#4b5563;margin-top:0}"
        print "table{border-collapse:collapse;width:100%}th,td{padding:.45rem .6rem;border-bottom:1px solid #e5e7eb;text-align:right}"
        print "th{position:sticky;top:0;background:#fff}th:nth-child(2),td:nth-child(2){text-align:left}"
        print ".chart{width:45%;min-width:260px}.bar{background:#2563eb;height:1.15rem;min-width:2px;border-radius:2px}"
        print "tbody tr:nth-child(-n+10) .bar{background:#dc2626}"
        print "</style></head><body>"
        print "<h1>TPC-DS per-query benchmark</h1>"
        printf "<p class=\"meta\">Database: <strong>%s</strong> | Run: %s | Measured executions per query: %s. Top 10 bars are highlighted.</p>\n", database, run_id, measured_runs
        print "<table><thead><tr><th>Rank</th><th>Query</th><th>Average (ms)</th><th>% of total time</th><th>Min (ms)</th><th>Max (ms)</th><th class=\"chart\">Relative average</th></tr></thead><tbody>"
    }
    NR > 1 {
        sum_total += $8
        sum_avg += $5
        width = max_average > 0 ? ($5 / max_average) * 100 : 0
        printf "<tr><td>%s</td><td>%s</td><td>%.1f</td><td>%.2f%%</td><td>%s</td><td>%s</td><td class=\"chart\"><div class=\"bar\" style=\"width:%.2f%%\"></div></td></tr>\n",
            $1, $2, $5, $9, $6, $7, width
    }
    END {
        print "</tbody></table>"
        printf "<p>Total elapsed time (5 iterations - 103 queries): %d ms - Average elapsed time per iteration (103 queries): %d ms.", sum_total, sum_avg
        print "<p class=\"meta\">Times include sqlcmd process startup overhead. See summary.csv and timings.csv for the underlying data.</p>"
        print "</body></html>"
    }
' "$SUMMARY_CSV" > "$REPORT_HTML"

AVERAGE_MS=$((TOTAL_SUM_MS / MEASURED_RUNS))
AVERAGE_SEC=$(awk "BEGIN {printf \"%.3f\", $AVERAGE_MS/1000}")

echo "=================================================="
echo "Per-query profile completed for $DB_NAME!"
echo "Total time for $MEASURED_RUNS measured runs : ${TOTAL_SUM_MS}ms"
echo "Average suite execution time   : ${AVERAGE_MS}ms (${AVERAGE_SEC}s)"
echo "Ranked timing summary          : $SUMMARY_CSV"
echo "Visual report                  : $REPORT_HTML"
echo "=================================================="
