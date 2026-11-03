#!/usr/bin/env bash
set -euo pipefail

OUT=$(mktemp -d "/benchmark_results/columnstores_profiler_$(date -u +%Y%m%dT%H%M%SZ)_XXXXXX")
exec > >(tee "$OUT/runner.log") 2>&1

printf 'database,average_ms,status,results_dir\n' > "$OUT/summary.csv"
echo "Results: $OUT/summary.csv"
failed=0

for scale in 1 2 5; do
    printf -v id '%02d' "$scale"

    for db in "tpcds_${id}_baseline" "tpcds_${id}"; do
        log="$OUT/${db}.log"
        echo "[$(date -Is)] Starting $db"

        if RESULTS_DIR="$OUT" \
            bash /scripts/04_profile_queries.sh "${scale}GB" "$db" \
            >"$log" 2>&1
        then
            average=$(awk '/^Average suite execution time/ {
                sub(/ms$/, "", $6)
                print $6
            }' "$log")
            results_dir=$(awk '/^Results: / {
                sub(/^Results: /, "", $0)
                print
                exit
            }' "$log")

            if [[ "$average" =~ ^[0-9]+$ && -n "$results_dir" ]]; then
                printf '%s,%s,OK,%s\n' "$db" "$average" "$results_dir" >> "$OUT/summary.csv"
                echo "[$(date -Is)] Finished $db: ${average} ms"
            else
                printf '%s,,INVALID_OUTPUT,%s\n' "$db" "$results_dir" >> "$OUT/summary.csv"
                echo "ERROR: Missing profiler summary for $db; inspect $log" >&2
                failed=1
            fi
        else
            printf '%s,,FAILED,\n' "$db" >> "$OUT/summary.csv"
            echo "ERROR: $db failed; inspect $log" >&2
            failed=1
        fi
    done
done

echo "[$(date -Is)] All six profiler runs attempted."
cat "$OUT/summary.csv"
exit "$failed"
