#!/bin/bash
set -e 

echo "Starting TPC-DS data generation..."

DEFAULT_SIZES=(1 2 5 10)
SIZES=()
SKIP_RECOMPILE=false
NO_PARALLEL=false
ONLY_QUERIES=false
SPLIT_QUERIES=false

while [ "$#" -gt 0 ]; do
    case "$1" in
        --no-recompile)
            SKIP_RECOMPILE=true
            ;;
        --no-parallel)
            NO_PARALLEL=true
            ;;
        --queryset)
            ONLY_QUERIES=true
            ;;
        --split-queries)
            SPLIT_QUERIES=true
            ;;
        -*)
            echo "Error: Unknown option: $1" >&2
            exit 1
            ;;
        *)
            SIZES+=("$1")
            ;;
    esac
    shift
done

if [ "${#SIZES[@]}" -eq 0 ]; then
    SIZES=("${DEFAULT_SIZES[@]}")
fi

for size in "${SIZES[@]}"; do
    if [[ ! "$size" =~ ^[1-9][0-9]*$ ]]; then
        echo "Error: Scale factors must be positive integers (received: $size)." >&2
        exit 1
    fi
done

if [ "$NO_PARALLEL" = false ]; then
    if ! command -v nproc >/dev/null 2>&1; then
        echo "Error: nproc is required to determine the available CPU count." >&2
        exit 1
    fi
    PARALLELISM=$(nproc)
    if [[ ! "$PARALLELISM" =~ ^[1-9][0-9]*$ ]]; then
        echo "Error: Could not determine a valid CPU count (received: $PARALLELISM)." >&2
        exit 1
    fi
fi

# 1. Navigate to the mounted tools directory
cd /tpcds-kit/tools

# 2. Clean previous Mac builds and compile for Linux
if [ "$SKIP_RECOMPILE" = false ]; then
    echo "Compiling dsdgen for Linux..."
    make -s clean
    make -s OS=LINUX > /dev/null 2>&1
fi

# 3. Generate the data for each requested scale factor
echo "Generating data for scale factors: ${SIZES[*]}GB ..."
if [ "$NO_PARALLEL" = true ]; then
    echo "Running without parallel generation."
else
    echo "Using $PARALLELISM parallel dsdgen processes."
fi

for size in "${SIZES[@]}"; do
    echo ""
    echo "    > Scale factor ${size}GB"

    # Generate query set for this scale factor
    queryset_dir="/data/${size}GB/queryset"
    mkdir -p "$queryset_dir"
    rm -rf "$queryset_dir"/*.sql
    ./dsqgen \
        -DIRECTORY ../query_templates \
        -INPUT ../query_templates/templates.lst \
        -QUIET Y \
        -QUALIFY Y \
        -SCALE "${size}" \
        -DIALECT sqlserver \
        -RNGSEED 56 \
        -OUTPUT_DIR "$queryset_dir"

    # Assuming generated file is named query_0.sql containing all queries
    mv -- "$queryset_dir/query_0.sql" "$queryset_dir/benchmark.sql"
    if [ "$SPLIT_QUERIES" = true ]; then
        : > "$queryset_dir/queries.lst"

        awk -v dir="$queryset_dir" '
        BEGIN { RS = ";" }
        {
            gsub(/^[[:space:]]+|[[:space:]]+$/, "")
            if ($0 == "") next

            n++
            file = sprintf("%s/query_%03d.sql", dir, n)
            print $0 ";" > file
            close(file)
            print sprintf("query_%03d.sql", n) >> (dir "/queries.lst")
        }
        ' "$queryset_dir/benchmark.sql"
    fi
    
    if [ "$ONLY_QUERIES" = true ]; then
        continue
    fi

    # Generate dataset for this scale factor
    dataset_dir="/data/${size}GB/dataset"
    mkdir -p "$dataset_dir"

    shopt -s nullglob
    existing_data_files=("$dataset_dir"/*.dat)
    if [ "${#existing_data_files[@]}" -gt 0 ]; then
        rm -- "${existing_data_files[@]}"
    fi

    if [ "$NO_PARALLEL" = true ]; then
        ./dsdgen -scale "$size" -dir "$dataset_dir" -force
        continue
    fi

    child_pids=()
    for ((child = 1; child <= PARALLELISM; child++)); do
        ./dsdgen -scale "$size" -dir "$dataset_dir" -force \
            -parallel "$PARALLELISM" -child "$child" &
        child_pids+=("$!")
    done

    generation_failed=false
    for child_pid in "${child_pids[@]}"; do
        if ! wait "$child_pid"; then
            generation_failed=true
        fi
    done

    if [ "$generation_failed" = true ]; then
        echo "Error: One or more dsdgen processes failed for scale factor ${size}GB." >&2
        exit 1
    fi

    declare -A shard_tables=()
    for file in "$dataset_dir"/*.dat; do
        filename=${file##*/}
        if [[ "$filename" =~ ^(.+)_([0-9]+)_([0-9]+)\.dat$ ]]; then
            shard_tables["${BASH_REMATCH[1]}"]=1
        fi
    done

    if [ "${#shard_tables[@]}" -eq 0 ]; then
        echo "Error: No partitioned .dat files found for scale factor ${size}GB." >&2
        exit 1
    fi

    echo "Combining generated partitions into one file per table..."
    for table in "${!shard_tables[@]}"; do
        shard_files=()
        for file in "$dataset_dir"/*.dat; do
            filename=${file##*/}
            if [[ "$filename" =~ ^(.+)_([0-9]+)_([0-9]+)\.dat$ ]] &&
                [ "${BASH_REMATCH[1]}" = "$table" ]; then
                shard_files+=("$file")
            fi
        done

        mapfile -t sorted_shard_files < <(printf '%s\n' "${shard_files[@]}" | sort -V)
        temporary_file="$dataset_dir/.${table}.dat.tmp.$$"
        cat "${sorted_shard_files[@]}" > "$temporary_file"
    done

    for table in "${!shard_tables[@]}"; do
        mv -- "$dataset_dir/.$table.dat.tmp.$$" "$dataset_dir/$table.dat"
    done

    for file in "$dataset_dir"/*.dat; do
        filename=${file##*/}
        if [[ "$filename" =~ ^(.+)_([0-9]+)_([0-9]+)\.dat$ ]]; then
            rm -- "$file"
        fi
    done
done

echo ""
echo "Data generation complete!"