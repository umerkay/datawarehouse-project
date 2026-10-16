
#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 || ! -f "$1" ]]; then
    echo "Usage: $0 input.sql" >&2
    exit 1
fi

mv -- "$1" benchmark.sql
rm -f query_*.sql
: > queries.lst

awk '
BEGIN { RS = ";" }
{
    gsub(/^[[:space:]]+|[[:space:]]+$/, "")
    if ($0 == "") next

    n++
    file = sprintf("query_%03d.sql", n)
    print $0 ";" > file
    close(file)
    print file >> "queries.lst"
}
' benchmark.sql

echo "Split $(wc -l < queries.lst) queries."
