#!/usr/bin/env bash
set -euo pipefail

OUT=$(mktemp -d "/benchmark_results/columnstores_$(date -u +%Y%m%dT%H%M%SZ)_XXXXXX")
exec > >(tee "$OUT/runner.log") 2>&1

printf 'database,average_ms,status\n' > "$OUT/summary.csv"
echo "Results: $OUT/summary.csv"
failed=0

for scale in 1 2 5; do
    printf -v id '%02d' "$scale"

    for db in "tpcds_${id}_baseline" "tpcds_${id}"; do
        log="$OUT/${db}.log"
        echo "[$(date -Is)] Starting $db"

        if WARMUP_RUNS=1 MEASURED_RUNS=5 \
            bash /scripts/03_run_benchmark.sh "${scale}GB" "$db" >"$log" 2>&1
        then
            average=$(awk '/^Average Execution Time/ {
                sub(/ms$/, "", $5)
                print $5
            }' "$log")

            if [[ "$average" =~ ^[0-9]+$ ]]; then
                printf '%s,%s,OK\n' "$db" "$average" >> "$OUT/summary.csv"
                echo "[$(date -Is)] Finished $db: ${average} ms"
            else
                printf '%s,,INVALID_OUTPUT\n' "$db" >> "$OUT/summary.csv"
                echo "ERROR: Missing average for $db; inspect $log" >&2
                failed=1
            fi
        else
            printf '%s,,FAILED\n' "$db" >> "$OUT/summary.csv"
            echo "ERROR: $db failed; inspect $log" >&2
            failed=1
        fi
    done
done

echo "[$(date -Is)] All six benchmarks attempted."
cat "$OUT/summary.csv"
exit "$failed"
