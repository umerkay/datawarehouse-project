#!/bin/bash
set -e 

echo "Starting TPC-DS data generation..."

# 1. Navigate to the mounted tools directory
cd /tpcds-kit/tools

# 2. Clean previous Mac builds and compile for Linux
echo "Compiling dsdgen for Linux..."
make clean
make OS=LINUX

# 3. Generate the data for all scale factors
echo "Generating data for scale factors: 1GB, 2GB, 5GB, 10GB..."
mkdir -p ../data_1GB ../data_2GB ../data_5GB ../data_10GB
# mkdir -p ../data_1GB

./dsdgen -scale 1 -dir ../data_1GB -force
./dsdgen -scale 2 -dir ../data_2GB -force
./dsdgen -scale 5 -dir ../data_5GB -force
./dsdgen -scale 10 -dir ../data_10GB -force

echo "Data generation complete!"