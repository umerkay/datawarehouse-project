#!/usr/bin/env bash
# Run INSIDE the SQL Server container. Prepare comparisons on a new restored copy.
set -euo pipefail
umask 022
original_args=("$@")

usage() {
    cat <<'HELP'
Usage: bash /scripts/benchmark.sh <1GB|2GB|5GB|10GB> <BASELINE_DB>
       [--list QUERY_INDEX ...] [--scenarios SCENARIO ...] [--dry-run]
       [--resume /benchmark_results/findings_<scale>_<id>]

Default: all listed queries; baseline, date_keys, date_shared (sales experiment).
Baseline = the seven IMCCI indexes from the main optimized schema; only sales indexes change.
Baseline is always first. One warm-up and five measured
workload passes per scenario. --list uses 1-based entries in queries.lst.
Creates a new private database using COPY_ONLY backup + restore. Never drops or
alters the source database. Additional disk space is required for backup, copy,
indexes and results. The copy and backup are retained for inspection.
HELP
}
die() { printf 'Error: %s\n' "$*" >&2; exit 1; }
if [[ ${1:-} == --help || ${1:-} == -h ]]; then usage; exit 0; fi
[[ $# -ge 2 ]] || { usage >&2; exit 1; }
scale=$1
source_db=$2
shift 2
case "$scale" in 1GB|2GB|5GB|10GB) ;; *) die 'Unsupported scale';; esac
[[ "$source_db" =~ ^[A-Za-z0-9_]{1,70}$ ]] || die 'Use a database name of 1-70 letters, digits or underscores'
indices=()
requested=()
dry_run=0
resume_dir=''
while [[ $# -gt 0 ]]; do
    case "$1" in
        --list)
            shift
            [[ $# -gt 0 && $1 != --* ]] || die '--list requires query indices'
            while [[ $# -gt 0 && $1 != --* ]]; do
                [[ $1 =~ ^[1-9][0-9]{0,8}$ ]] || die "Invalid query index: $1"
                indices+=("$1"); shift
            done;;
        --scenarios)
            shift
            [[ $# -gt 0 && $1 != --* ]] || die '--scenarios requires scenario names'
            while [[ $# -gt 0 && $1 != --* ]]; do requested+=("$1"); shift; done;;
        --dry-run) dry_run=1; shift;;
        --resume) [[ $# -ge 2 ]] || die '--resume requires a results directory'; resume_dir=$2; shift 2;;
        *) die "Unknown argument: $1";;
    esac
done
if [[ ${#requested[@]} -eq 0 ]]; then requested=(date_keys date_shared); fi
scenarios=(baseline)
for scenario in "${requested[@]}"; do
    case "$scenario" in baseline|date_keys|date_shared) ;; *) die "Unknown scenario: $scenario";; esac
    if [[ " ${scenarios[*]} " != *" $scenario "* ]]; then scenarios+=("$scenario"); fi
done
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
schema_file=$script_dir/cci_schema.sql
if [[ ! -f $schema_file ]]; then schema_file=$script_dir/../../tpcds-optimized/tools/tpcds.sql; fi
query_dir=${QUERY_DIR:-/data/$scale/queryset}
[[ -f "$query_dir/queries.lst" ]] || die "Missing $query_dir/queries.lst; generate split queries first"
mapfile -t files < "$query_dir/queries.lst"
[[ ${#files[@]} -gt 0 ]] || die 'Empty queries.lst'
if [[ ${#indices[@]} -eq 0 ]]; then
    for ((i=1;i<=${#files[@]};i++)); do indices+=("$i"); done
fi
selected=()
declare -A seen_indices=() seen_files=()
for i in "${indices[@]}"; do
    ((i <= ${#files[@]})) || die "Query index $i exceeds ${#files[@]} entries"
    file=${files[i-1]%$'\r'}
    [[ "$file" =~ ^[A-Za-z0-9_.-]+\.sql$ && "$file" != *..* ]] || die "Invalid query filename: $file"
    [[ -f "$query_dir/$file" ]] || die "Missing query: $file"
    if [[ -z ${seen_indices[$i]:-} ]]; then
        [[ -z ${seen_files[$file]:-} ]] || die "Duplicate query file in manifest: $file"
        selected+=("$file"); seen_indices[$i]=1; seen_files[$file]=1
    fi
done
printf 'Source: %s; scale/queryset: %s\nScenarios: %s\nQueries (%d): %s\n' "$source_db" "$scale" "${scenarios[*]}" "${#selected[@]}" "${selected[*]}"
echo 'Protocol: one warm-up + five measured passes; identical selected SQL in every scenario.'
if ((dry_run)); then echo 'Dry run: no SQL connection, backup, database or files created.'; exit 0; fi

sqlcmd_bin=${SQLCMD_BIN:-/opt/mssql-tools18/bin/sqlcmd}
[[ -x "$sqlcmd_bin" ]] || die "sqlcmd is not executable: $sqlcmd_bin"
export SQLCMDPASSWORD=${SQLCMDPASSWORD:-${MSSQL_SA_PASSWORD:-}}
[[ -n "$SQLCMDPASSWORD" ]] || die 'Set SQLCMDPASSWORD or MSSQL_SA_PASSWORD'
server=${SQL_SERVER:-localhost}
user=${SQL_USER:-sa}
timeout=${QUERY_TIMEOUT:-3600}
[[ "$timeout" =~ ^[1-9][0-9]{0,8}$ ]] || die 'QUERY_TIMEOUT must be positive seconds'
for tool in awk sort sha256sum gzip date tr; do command -v "$tool" >/dev/null || die "Missing utility: $tool"; done
[[ -f "$script_dir/indexes.sql" && -f "$schema_file" ]] || die "Missing indexes.sql or tpcds-optimized/tools/tpcds.sql"
if [[ ${FINDINGS_RUNNER_FROZEN:-0} != 1 ]]; then
    version_root=${RESULTS_DIR:-/benchmark_results}/.runner_versions
    mkdir -p "$version_root"
    frozen_dir=$(mktemp -d "$version_root/runner.XXXXXX")
    cp -- "$script_dir/benchmark.sh" "$script_dir/indexes.sql" "$frozen_dir/"
    cp -- "$schema_file" "$frozen_dir/cci_schema.sql"
    export FINDINGS_RUNNER_FROZEN=1
    exec bash "$frozen_dir/benchmark.sh" "${original_args[@]}"
fi
backup_dir=${SQL_BACKUP_DIR:-/var/opt/mssql/backup}
data_dir=${SQL_DATA_DIR:-/var/opt/mssql/data}
[[ "$backup_dir" =~ ^/[A-Za-z0-9_./-]+$ && "$data_dir" =~ ^/[A-Za-z0-9_./-]+$ ]] || die 'SQL backup/data directories must be simple absolute server-local paths'
run_id=$(date -u +%Y%m%dT%H%M%SZ)_${RANDOM}
experiment_db=${source_db}_findings_${run_id}
output_root=${RESULTS_DIR:-/benchmark_results}/findings_${scale}_${run_id}
backup_file=$backup_dir/$experiment_db.bak
if [[ -n $resume_dir ]]; then
    [[ -d $resume_dir && ! -L $resume_dir && -f $resume_dir/manifest.txt ]] || die 'Invalid resume directory'
    output_root=$(realpath -- "$resume_dir")
    [[ $(awk -F= '$1=="protocol"{print $2}' "$output_root/manifest.txt") == cci_main_v3 ]] || die 'Old protocol cannot be resumed; start a fresh columnstore-base experiment'
    [[ -n $(awk -F= '$1=="base_validated"{print $2}' "$output_root/manifest.txt") ]] || die 'Source CCI validation did not finish; start a fresh experiment'
    for spec in 'index_definition_sha256:indexes.sql' 'schema_sha256:cci_schema.sql' 'runner_sha256:benchmark.sh'; do
        key=${spec%%:*}; file=${spec#*:}
        [[ $(awk -F= -v key="$key" '$1==key{print $2}' "$output_root/manifest.txt") == "$(sha256sum "$script_dir/$file" | awk '{print $1}')" ]] || die "Resume code changed: $file"
    done
    saved_source=$(awk -F= '$1=="source_database"{print $2}' "$output_root/manifest.txt")
    saved_scale=$(awk -F= '$1=="scale"{print $2}' "$output_root/manifest.txt")
    saved_scenarios=$(awk -F= '$1=="scenarios"{print $2}' "$output_root/manifest.txt")
    saved_timeout=$(awk -F= '$1=="query_timeout_seconds"{print $2}' "$output_root/manifest.txt")
    experiment_db=$(awk -F= '$1=="experiment_database"{print $2}' "$output_root/manifest.txt")
    backup_file=$(awk -F= '$1=="backup"{print $2}' "$output_root/manifest.txt")
    [[ $saved_source == "$source_db" && $saved_scale == "$scale" && $saved_scenarios == "${scenarios[*]}" && $saved_timeout == "$timeout" ]] || die 'Resume source, scale, scenarios and timeout must match the original run'
    [[ $experiment_db =~ ^${source_db}_findings_[A-Za-z0-9_]+$ && ${#experiment_db} -le 128 ]] || die 'Invalid private database in manifest'
    cmp -s <(printf '%s\n' "${selected[@]}") "$output_root/queries.lst" || die 'Resume query selection differs from original'
else
[[ ! -e "$backup_file" ]] || die "Backup path already exists: $backup_file"
mkdir -p "$(dirname -- "$output_root")" "$backup_dir"
mkdir -- "$output_root"
fi
sql=("$sqlcmd_bin" -b -r 1 -S "$server" -U "$user" -C)
run_sql() { "${sql[@]}" -d "$experiment_db" "$@"; }
on_exit() {
    local status=$?
    if ((status != 0)); then
        printf 'FAILED\n' > "$output_root/STATUS"
        printf 'Experiment stopped. Results: %s\nPrivate database (if created): %s\nSource retained: %s\n' "$output_root" "$experiment_db" "$source_db" >&2
    fi
}
trap on_exit EXIT
if [[ -z $resume_dir ]]; then
printf 'source_database=%s\nexperiment_database=%s\nscale=%s\nquery_timeout_seconds=%s\nscenarios=%s\nbackup=%s\n' "$source_db" "$experiment_db" "$scale" "$timeout" "${scenarios[*]}" "$backup_file" > "$output_root/manifest.txt"
printf 'runner_snapshot=%s\nindex_definition_sha256=%s\n' "$script_dir" "$(sha256sum "$script_dir/indexes.sql" | awk '{print $1}')" >> "$output_root/manifest.txt"
printf 'protocol=cci_main_v3\nbase_storage=clustered_columnstore_all_7_facts\nschema_sha256=%s\nrunner_sha256=%s\n' "$(sha256sum "$script_dir/cci_schema.sql" | awk '{print $1}')" "$(sha256sum "$script_dir/benchmark.sh" | awk '{print $1}')" >> "$output_root/manifest.txt"
cp -- "$script_dir/cci_schema.sql" "$output_root/cci_schema.sql"
printf '%s\n' "${selected[@]}" > "$output_root/queries.lst"
fi
declare -A sql_hash=()
current_hashes=$(mktemp "$output_root/.current_hashes.XXXXXX")
for file in "${selected[@]}"; do
    sql_hash[$file]=$(sha256sum "$query_dir/$file" | awk '{print $1}')
    printf '%s  %s\n' "${sql_hash[$file]}" "$query_dir/$file"
done > "$current_hashes"
if [[ -n $resume_dir ]]; then
    cmp -s "$current_hashes" "$output_root/query_sha256.txt" || { rm -- "$current_hashes"; die 'SQL fingerprints changed; cannot reuse old measurements'; }
    rm -- "$current_hashes"
else
    mv -- "$current_hashes" "$output_root/query_sha256.txt"
fi
# Keep exact SQL inputs alongside measurements for independent audit.
mkdir -p "$output_root/source_queries"
for file in "${selected[@]}"; do cp -- "$query_dir/$file" "$output_root/source_queries/$file"; done
printf 'RUNNING\n' > "$output_root/STATUS"

# Validate before backing up. No SQL mutation of the source or other databases.
if [[ -z $resume_dir ]]; then
"${sql[@]}" -d master -t 0 -Q "
SET NOCOUNT ON;
IF DB_ID(N'$source_db') IS NULL THROW 51000, 'Source database does not exist', 1;
IF DB_ID(N'$experiment_db') IS NOT NULL THROW 51000, 'Experiment database already exists', 1;
IF EXISTS (SELECT 1 FROM sys.master_files WHERE database_id=DB_ID(N'$source_db') AND type NOT IN (0,1))
    THROW 51000, 'Only ordinary data and log files are supported', 1;
USE [$source_db];
IF OBJECT_ID(N'dbo.store_sales',N'U') IS NULL OR OBJECT_ID(N'dbo.catalog_sales',N'U') IS NULL OR OBJECT_ID(N'dbo.web_sales',N'U') IS NULL
    THROW 51000, 'Source must contain the populated TPC-DS sales tables', 1;
IF (SELECT COUNT(*) FROM sys.tables WHERE schema_id=SCHEMA_ID(N'dbo') AND name IN
    (N'store_sales',N'catalog_sales',N'web_sales',N'store_returns',N'catalog_returns',N'web_returns',N'inventory')) <> 7
    THROW 51000, 'All seven TPC-DS fact tables are required', 1;
IF EXISTS (SELECT 1 FROM sys.indexes i JOIN sys.tables t ON t.object_id=i.object_id
    WHERE t.is_ms_shipped=0 AND i.index_id>0 AND
      (i.is_disabled=1 OR i.is_hypothetical=1 OR (i.is_primary_key=0 AND i.is_unique_constraint=0 AND NOT
       (i.type=5 AND t.schema_id=SCHEMA_ID(N'dbo') AND t.name IN
       (N'store_sales',N'catalog_sales',N'web_sales',N'store_returns',N'catalog_returns',N'web_returns',N'inventory')))))
    THROW 51000, 'Use the main CCI schema with original constraints, without other tuning indexes', 1;
IF (SELECT COUNT(*) FROM sys.indexes i JOIN sys.tables t ON t.object_id=i.object_id
    WHERE t.schema_id=SCHEMA_ID(N'dbo') AND t.name IN
    (N'store_sales',N'catalog_sales',N'web_sales',N'store_returns',N'catalog_returns',N'web_returns',N'inventory')
    AND i.type=5 AND i.is_disabled=0 AND i.name=N'IMCCI_'+t.name) <> 7
    THROW 51000, 'Source must already contain the seven IMCCI indexes from tpcds-optimized/tools/tpcds.sql', 1;
IF (SELECT COUNT(*) FROM sys.indexes i JOIN sys.tables t ON t.object_id=i.object_id
    WHERE t.schema_id=SCHEMA_ID(N'dbo') AND t.name IN
    (N'store_sales',N'catalog_sales',N'web_sales',N'store_returns',N'catalog_returns',N'web_returns',N'inventory')
    AND i.is_primary_key=1 AND i.type=2 AND i.is_disabled=0) <> 7
    THROW 51000, 'Source must retain seven nonclustered fact primary keys', 1;
IF NOT EXISTS(SELECT 1 FROM dbo.store_sales) OR NOT EXISTS(SELECT 1 FROM dbo.catalog_sales) OR NOT EXISTS(SELECT 1 FROM dbo.web_sales)
    THROW 51000, 'Source sales tables must not be empty', 1;
SELECT @@VERSION AS server_version;
SELECT cpu_count, scheduler_count, physical_memory_kb, sqlserver_start_time FROM sys.dm_os_sys_info;
SELECT name,value_in_use FROM sys.configurations WHERE name IN (N'max degree of parallelism',N'cost threshold for parallelism',N'max server memory (MB)',N'min server memory (MB)');
SELECT name,compatibility_level,is_auto_create_stats_on,is_auto_update_stats_on,is_auto_update_stats_async_on
FROM sys.databases WHERE name=N'$source_db';
" > "$output_root/source_metadata.txt"

echo "Creating a COPY_ONLY backup and restoring private database $experiment_db"
"${sql[@]}" -d master -t 0 -Q "
SET NOCOUNT ON;
BACKUP DATABASE [$source_db] TO DISK=N'$backup_file' WITH COPY_ONLY, CHECKSUM, COMPRESSION;
DECLARE @moves nvarchar(max), @restore nvarchar(max);
SELECT @moves=STRING_AGG(CONVERT(nvarchar(max),N'MOVE '+QUOTENAME(name,'''')+N' TO '+
 QUOTENAME(N'$data_dir/${experiment_db}_'+CONVERT(nvarchar(10),file_id)+CASE WHEN type=1 THEN N'.ldf' ELSE N'.mdf' END,'''')),N',')
FROM sys.master_files WHERE database_id=DB_ID(N'$source_db');
SET @restore=N'RESTORE DATABASE [$experiment_db] FROM DISK=N''$backup_file'' WITH '+@moves+N', RECOVERY, CHECKSUM;';
EXEC sys.sp_executesql @restore;
" > "$output_root/clone.log" 2>&1

printf 'schema,table,row_count\n' > "$output_root/table_rows.csv"
run_sql -h -1 -W -s , -Q "SET NOCOUNT ON; SELECT s.name,t.name,SUM(p.row_count) FROM sys.dm_db_partition_stats p JOIN sys.tables t ON t.object_id=p.object_id JOIN sys.schemas s ON s.schema_id=t.schema_id WHERE p.index_id IN(0,1) GROUP BY s.name,t.name ORDER BY s.name,t.name;" >> "$output_root/table_rows.csv"
printf 'base_validated=1\n' >> "$output_root/manifest.txt"
printf 'scenario,query_id,baseline_ms,average_ms,change_percent,speedup,output_check\n' > "$output_root/comparison.csv"
printf 'scenario,selected_queries,sum_average_ms,index_reserved_kb,setup_ms,output_reviews\n' > "$output_root/workload.csv"
else
    "${sql[@]}" -d master -t 0 -Q "SET NOCOUNT ON; IF DB_ID(N'$experiment_db') IS NULL THROW 51000, 'Private experiment database is missing', 1; SELECT @@VERSION; SELECT name,compatibility_level,is_auto_create_stats_on,is_auto_update_stats_on FROM sys.databases WHERE name=N'$experiment_db';" > "$output_root/resume_metadata_$run_id.txt"
    printf '%s resumed; SQL verified; incomplete scenarios restarted with fresh warm-up. Baseline predates the interruption.\n' "$(date -u +%FT%TZ)" >> "$output_root/resume_events.log"
fi
declare -A reference_hash=()

phase_complete() {
    local folder=$1 file
    for file in timings.csv summary.csv result_checks.csv; do [[ -f $folder/$file ]] || return 1; done
    awk -F, '
        FILENAME==ARGV[1] {q=$0;sub(/\.sql$/,"",q);want[q]=1;next}
        FILENAME==ARGV[2] {if(FNR==1)next;if(!($2 in want)||$1!~/^[1-5]$/||$3!~/^[0-9]+$/||seen[$2 SUBSEP $1]++)bad=1;n[$2]++;total[$2]+=$3;next}
        FILENAME==ARGV[3] {if(FNR==1)next;if(!($1 in want)||summary[$1]++||$6!=5||n[$1]!=5||sprintf("%.1f",total[$1]/5)!=$2)bad=1;next}
        FILENAME==ARGV[4] {if(FNR==1)next;if(!($1 in want)||checks[$1]++||length($3)!=64||$3~/[^0-9a-f]/)bad=1}
        END {for(q in want)if(n[q]!=5||summary[q]!=1||checks[q]!=1)bad=1;exit bad?1:0}
    ' "$output_root/queries.lst" "$folder/timings.csv" "$folder/summary.csv" "$folder/result_checks.csv"
}
completed=' '
if [[ -n $resume_dir ]]; then
    for scenario in "${scenarios[@]}"; do
        if phase_complete "$output_root/$scenario"; then completed+="$scenario "; fi
    done
    # Preserve original aggregates and remove records for any incomplete phase.
    for csv in comparison workload; do
        cp -- "$output_root/$csv.csv" "$output_root/${csv}.before_resume_${run_id}.csv"
        awk -F, -v done="$completed" 'NR==1||index(done," "$1" ")>0' "$output_root/${csv}.before_resume_${run_id}.csv" > "$output_root/$csv.csv"
    done
fi

for scenario in "${scenarios[@]}"; do
    phase_dir=$output_root/$scenario
    if [[ $completed == *" $scenario "* ]]; then
        echo "[$scenario] Preserving complete scenario (five validated measurements per query)"
        if [[ $scenario == baseline ]]; then
            while IFS=, read -r id check digest; do
                [[ $id == query_id ]] && continue
                reference_hash[$id]=$digest
            done < "$phase_dir/result_checks.csv"
        fi
        continue
    fi
    if [[ -n $resume_dir && -d $phase_dir ]]; then
        mv -- "$phase_dir" "$output_root/${scenario}.interrupted_${run_id}"
    fi
    mkdir -p "$phase_dir/plans" "$phase_dir/results"
    echo "[$scenario] Preparing indexes, plans and FULLSCAN statistics on $experiment_db"
    start=$(date +%s%3N)
    run_sql -t 0 -v SCENARIO="$scenario" EXPERIMENT_DB="$experiment_db" -i "$script_dir/indexes.sql" > "$phase_dir/setup.log" 2>&1
    setup_ms=$(( $(date +%s%3N) - start ))

    # Compile selected queries before final statistics normalization, so any
    # automatic statistics needed by these statements can also be refreshed.
    for file in "${selected[@]}"; do
        wrapper=$phase_dir/plan_wrapper.sql
        printf 'SET SHOWPLAN_XML ON;\nGO\n:r "%s"\nGO\nSET SHOWPLAN_XML OFF;\nGO\n' "$query_dir/$file" > "$wrapper"
        run_sql -t "$timeout" -y 0 -w 65535 -i "$wrapper" -o "$phase_dir/plans/${file%.sql}.xml"
    done
    # Apply the same explicit FULLSCAN protocol after compilation, including
    # any automatic statistics created while compiling the selected queries.
    run_sql -Q "SET NOCOUNT ON; DECLARE @q nvarchar(max); SELECT @q=STRING_AGG(CONVERT(nvarchar(max),N'UPDATE STATISTICS '+QUOTENAME(s.name)+N'.'+QUOTENAME(t.name)+N' WITH FULLSCAN;'),CHAR(10)) FROM sys.tables t JOIN sys.schemas s ON s.schema_id=t.schema_id WHERE t.is_ms_shipped=0; EXEC sys.sp_executesql @q;" > "$phase_dir/statistics.log"
    # Clear compiled plans once after preparation, BEFORE warm-up. This does not
    # clear data buffers; measured passes retain the warm-up's cached plans.
    run_sql -Q 'ALTER DATABASE SCOPED CONFIGURATION CLEAR PROCEDURE_CACHE;' >> "$phase_dir/statistics.log"
    # Capture the normalized estimated plans used for interpretation, untimed.
    for file in "${selected[@]}"; do
        printf 'SET SHOWPLAN_XML ON;\nGO\n:r "%s"\nGO\nSET SHOWPLAN_XML OFF;\nGO\n' "$query_dir/$file" > "$wrapper"
        run_sql -t "$timeout" -y 0 -w 65535 -i "$wrapper" -o "$phase_dir/plans/${file%.sql}.xml"
    done
    rm -f -- "$wrapper"
    printf 'schema,table,index_name,index_type,reserved_kb\n' > "$phase_dir/indexes.csv"
    run_sql -h -1 -W -s , -Q "SET NOCOUNT ON; SELECT s.name,t.name,i.name,i.type_desc,SUM(p.reserved_page_count)*8 FROM sys.indexes i JOIN sys.tables t ON t.object_id=i.object_id JOIN sys.schemas s ON s.schema_id=t.schema_id JOIN sys.dm_db_partition_stats p ON p.object_id=i.object_id AND p.index_id=i.index_id WHERE i.name LIKE N'FINDINGS[_]%' GROUP BY s.name,t.name,i.name,i.type_desc ORDER BY t.name,i.name;" >> "$phase_dir/indexes.csv"
    printf 'schema,table,index_name,index_type,column,key_ordinal,included,index_column_id\n' > "$phase_dir/index_columns.csv"
    run_sql -h -1 -W -s , -Q "SET NOCOUNT ON; SELECT s.name,t.name,i.name,i.type_desc,c.name,ic.key_ordinal,ic.is_included_column,ic.index_column_id FROM sys.indexes i JOIN sys.tables t ON t.object_id=i.object_id JOIN sys.schemas s ON s.schema_id=t.schema_id JOIN sys.index_columns ic ON ic.object_id=i.object_id AND ic.index_id=i.index_id JOIN sys.columns c ON c.object_id=ic.object_id AND c.column_id=ic.column_id WHERE i.name LIKE N'FINDINGS[_]%' ORDER BY t.name,i.name,ic.index_column_id;" >> "$phase_dir/index_columns.csv"
    index_kb=$(run_sql -h -1 -W -Q "SET NOCOUNT ON; SELECT COALESCE(SUM(p.reserved_page_count)*8,0) FROM sys.dm_db_partition_stats p JOIN sys.indexes i ON i.object_id=p.object_id AND i.index_id=p.index_id WHERE i.name LIKE N'FINDINGS[_]%';" | tr -d '[:space:]')
    # Full physical inventory includes shared CCIs and PKs. The FINDINGS-only
    # number above represents additional B-tree storage relative to the CCI base.
    printf 'schema,table,index_name,index_type,reserved_kb\n' > "$phase_dir/all_indexes.csv"
    run_sql -h -1 -W -s , -Q "SET NOCOUNT ON; SELECT s.name,t.name,COALESCE(i.name,N'HEAP'),i.type_desc,SUM(p.reserved_page_count)*8 FROM sys.indexes i JOIN sys.tables t ON t.object_id=i.object_id JOIN sys.schemas s ON s.schema_id=t.schema_id JOIN sys.dm_db_partition_stats p ON p.object_id=i.object_id AND p.index_id=i.index_id GROUP BY s.name,t.name,i.name,i.type_desc ORDER BY s.name,t.name,i.name;" >> "$phase_dir/all_indexes.csv"
    printf 'run,query_id,duration_ms\n' > "$phase_dir/timings.csv"
    printf 'query_id,output_check,sha256\n' > "$phase_dir/result_checks.csv"
    reviews=0
    for run in {0..5}; do
        for file in "${selected[@]}"; do
            query_id=${file%.sql}
            # Abort if a local SQL edit would invalidate this comparison.
            current_hash=$(sha256sum "$query_dir/$file" | awk '{print $1}')
            [[ $current_hash == "${sql_hash[$file]}" ]] || die "SQL changed during experiment: $file"
            printf '[%s] %s run %d/5%s\n' "$scenario" "$query_id" "$run" "$([[ $run == 0 ]] && echo ' (warm-up)' || true)"
            destination=/dev/null
            if ((run == 0)); then destination=$phase_dir/results/$query_id.txt; fi
            start=$(date +%s%3N)
            run_sql -t "$timeout" -y 0 -w 65535 -i "$query_dir/$file" -o "$destination"
            duration=$(( $(date +%s%3N) - start ))
            if ((run > 0)); then
                printf '%d,%s,%d\n' "$run" "$query_id" "$duration" >> "$phase_dir/timings.csv"
            else
                digest=$(sha256sum "$destination" | awk '{print $1}')
                check=reference
                if [[ $scenario == baseline ]]; then reference_hash[$query_id]=$digest
                elif [[ $digest == "${reference_hash[$query_id]}" ]]; then check=raw_match
                else check=review_required; reviews=$((reviews+1)); echo "Output differs for $query_id: inspect result ordering/values before claiming equivalence." >&2
                fi
                printf '%s,%s,%s\n' "$query_id" "$check" "$digest" >> "$phase_dir/result_checks.csv"
                gzip -n -- "$destination"
            fi
        done
    done
    {
        printf 'query_id,average_ms,min_ms,max_ms,total_ms,runs\n'
        awk -F, 'NR>1 {n[$2]++;total[$2]+=$3;if(!($2 in lo)||$3<lo[$2])lo[$2]=$3;if(!($2 in hi)||$3>hi[$2])hi[$2]=$3} END {for(q in n) {if(n[q]!=5)exit 1;printf "%s,%.1f,%d,%d,%d,%d\n",q,total[q]/n[q],lo[q],hi[q],total[q],n[q]}}' "$phase_dir/timings.csv" | sort -t, -k2,2nr
    } > "$phase_dir/summary.csv"
    if [[ $scenario != baseline ]]; then
        awk -F, -v phase="$scenario" -v checks="$phase_dir/result_checks.csv" '
            FILENAME==ARGV[1] {if(FNR>1)base[$1]=$2;next}
            FILENAME==checks {if(FNR>1)status[$1]=$2;next}
            FNR>1 {change=base[$1]>0?sprintf("%.2f",100*($2/base[$1]-1)):"NA";speed=$2>0?sprintf("%.3f",base[$1]/$2):"NA";printf "%s,%s,%s,%s,%s,%s,%s\n",phase,$1,base[$1],$2,change,speed,status[$1]}
        ' "$output_root/baseline/summary.csv" "$phase_dir/result_checks.csv" "$phase_dir/summary.csv" >> "$output_root/comparison.csv"
    fi
    sum_ms=$(awk -F, 'NR>1{sum+=$2}END{printf "%.1f",sum}' "$phase_dir/summary.csv")
    printf '%s,%d,%s,%s,%d,%d\n' "$scenario" "${#selected[@]}" "$sum_ms" "$index_kb" "$setup_ms" "$reviews" >> "$output_root/workload.csv"
done
printf 'COMPLETE\n' > "$output_root/STATUS"
printf 'Complete. Results: %s\nComparison: %s/comparison.csv\nPrivate database retained: %s\nBackup retained: %s\n' "$output_root" "$output_root" "$experiment_db" "$backup_file"
echo 'Negative change_percent means faster. Review output differences; raw text equality is not a full semantic validation.'
