#!/bin/bash
set -e 

echo "Starting TPC-DS data generation..."

DEFAULT_SIZES=(1 2 5 10)
SIZES=("${@:-}")
if [ "$#" -eq 0 ]; then
    SIZES=("${DEFAULT_SIZES[@]}")
fi

for size in "${SIZES[@]}"; do
    if [[ ! "$size" =~ ^[1-9][0-9]*$ ]]; then
        echo "Error: Scale factors must be positive integers (received: $size)." >&2
        exit 1
    fi
done

# 1. Navigate to the mounted tools directory
cd /tpcds-kit/tools

# 2. Clean previous Mac builds and compile for Linux
echo "Compiling dsdgen for Linux..."
make -s clean
make -s OS=LINUX > /dev/null 2>&1

# 3. Generate the data for each requested scale factor
echo "Generating data for scale factors: ${SIZES[*]}GB ..."
for size in "${SIZES[@]}"; do
    echo ""
    echo "    > Scale factor ${size}GB"
    output_dir="../data_${size}GB"
    mkdir -p "$output_dir"
    ./dsdgen -scale "$size" -dir "$output_dir" -force
done

echo ""
echo "Data generation complete!"