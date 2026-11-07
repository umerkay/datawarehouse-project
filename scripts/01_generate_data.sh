#!/bin/bash
set -e 

echo "Starting TPC-DS data generation..."

DEFAULT_SIZES=(1 2 5 10)
SIZES=()
SKIP_RECOMPILE=false
NO_PARALLEL=false
ONLY_QUERIES=false
SPLIT_QUERIES=false

SEED=56

# The repository is frequently checked out on Windows but the scripts execute
# in Linux containers. Normalize all mounted shell/SQL control files up front.
find /scripts -type f \( -name '*.sh' -o -name '*.sql' \) \
    -exec sed -i 's/\r$//' {} +

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

# dsqgen treats a trailing carriage return in templates.lst as part of the
# template filename (for example, query1.tpl\r), so normalize both lists.
sed -i 's/\r$//' \
    /tpcds-kit/query_templates/templates.lst \
    /tpcds-optimized/query_templates/templates.lst
find /tpcds-kit/query_templates /tpcds-optimized/query_templates \
    -maxdepth 1 -type f -name '*.tpl' -exec sed -i 's/\r$//' {} +

# 2. Clean previous Mac builds and compile for Linux
if [ "$SKIP_RECOMPILE" = false ]; then
    echo "Compiling dsdgen for Linux..."
    # Git checkouts on Windows may leave CRLF in distcomp inputs. distcomp can
    # segfault on that input while generating tpcds.idx, so normalize the small
    # metadata files before compilation. The generated data itself is unchanged.
    for metadata in *.dst; do
        sed -i 's/\r$//' "$metadata"
        if ! iconv -f UTF-8 -t UTF-8 "$metadata" >/dev/null 2>&1; then
            iconv -f ISO-8859-1 -t UTF-8 "$metadata" > "$metadata.utf8"
            mv -- "$metadata.utf8" "$metadata"
        fi
    done
    sed -i 's/\r$//' column_list.txt
    # Repair a malformed nested add() token present in this repository copy.
    sed -i 's/add ("add ("COOK ISLANDS":1);/add ("COOK ISLANDS":1);/' tpcds.dst
    make -s clean
    # Keep compiler output visible: silent failures otherwise make unattended
    # scale generation impossible to diagnose.
    make -s OS=LINUX
fi

# 3. Generate the data for each requested scale factor
echo "Generating data for scale factors: ${SIZES[*]}GB ..."
if [ "$NO_PARALLEL" = true ]; then
    echo "Running without parallel generation."
else
    echo "Using $PARALLELISM parallel dsdgen processes."
fi

generate_queries() {
    local size=$1
    local is_optimized=$2
    local queryset_dir="/data/${size}GB/queryset"
    local tpcds_base_dir="tpcds-kit"
    if [ "$is_optimized" = true ]; then
        queryset_dir="/data/${size}GB/queryset-optimized"
        tpcds_base_dir="tpcds-optimized"
    fi

    mkdir -p "$queryset_dir"
    rm -rf "$queryset_dir"/*.sql
    ./dsqgen \
        -DIRECTORY /$tpcds_base_dir/query_templates \
        -INPUT /$tpcds_base_dir/query_templates/templates.lst \
        -QUIET Y \
        -QUALIFY Y \
        -SCALE "${size}" \
        -DIALECT sqlserver \
        -RNGSEED $SEED \
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
    chmod -R a+rwX "$queryset_dir"
}

for size in "${SIZES[@]}"; do
    echo ""
    echo "    > Scale factor ${size}GB"

    # Generate querysets for this scale factor
    generate_queries $size false # From original  query templates
    generate_queries $size true  # From optimized query templates
    
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
        ./dsdgen \
            -SCALE "$size" \
            -DIR "$dataset_dir" \
            -RNGSEED $SEED \
            -FORCE
        continue
    fi

    child_pids=()
    for ((child = 1; child <= PARALLELISM; child++)); do
        ./dsdgen \
            -SCALE "$size" \
            -DIR "$dataset_dir" \
            -RNGSEED $SEED \
            -PARALLEL "$PARALLELISM" \
            -CHILD "$child" \
            -FORCE &
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
