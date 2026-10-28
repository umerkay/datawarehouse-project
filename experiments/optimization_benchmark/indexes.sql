/* Used only on the disposable database created by benchmark.sh.
   The runner validates SCENARIO before supplying this sqlcmd variable. */
SET NOCOUNT ON;
SET XACT_ABORT ON;
IF DB_NAME() <> N'$(EXPERIMENT_DB)' OR CHARINDEX(N'_findings_', DB_NAME()) = 0
    THROW 51000, 'Findings indexes must be applied only to the private experiment database', 1;
DECLARE @scenario sysname = N'$(SCENARIO)';
IF @scenario NOT IN (N'baseline', N'date_keys', N'date_shared', N'date_priority', N'columnstore', N'columnstore_date_keys')
    THROW 51000, 'Unknown findings scenario', 1;

-- Remove only indexes owned by this experiment, on its private database.
DECLARE @sql nvarchar(max), @name sysname, @table sysname;
DECLARE old_indexes CURSOR LOCAL FAST_FORWARD FOR
SELECT i.name, t.name FROM sys.indexes i
JOIN sys.tables t ON t.object_id = i.object_id
JOIN sys.schemas s ON s.schema_id = t.schema_id
WHERE s.name = N'dbo' AND t.name IN (N'store_sales', N'catalog_sales', N'web_sales')
  AND i.name IN (N'FINDINGS_store_date', N'FINDINGS_catalog_date', N'FINDINGS_web_date',
                 N'FINDINGS_store_columnstore', N'FINDINGS_catalog_columnstore', N'FINDINGS_web_columnstore');
OPEN old_indexes;
FETCH NEXT FROM old_indexes INTO @name, @table;
WHILE @@FETCH_STATUS = 0
BEGIN
    SET @sql = N'DROP INDEX ' + QUOTENAME(@name) + N' ON dbo.' + QUOTENAME(@table);
    EXEC sys.sp_executesql @sql;
    FETCH NEXT FROM old_indexes INTO @name, @table;
END;
CLOSE old_indexes;
DEALLOCATE old_indexes;

IF @scenario IN (N'date_keys', N'date_shared', N'date_priority', N'columnstore_date_keys')
BEGIN
    SET @sql = N'CREATE NONCLUSTERED INDEX FINDINGS_store_date ON dbo.store_sales (ss_sold_date_sk)';
    IF @scenario = N'date_shared'
        SET @sql += N' INCLUDE (ss_customer_sk, ss_quantity, ss_sales_price)';
    IF @scenario = N'date_priority'
        SET @sql += N' INCLUDE (ss_quantity, ss_list_price)';
    EXEC sys.sp_executesql @sql;
    -- Keep the other two indexes identical across the three rowstore scenarios:
    -- date_shared vs date_priority tests only the store_sales INCLUDE choice.
    CREATE NONCLUSTERED INDEX FINDINGS_catalog_date ON dbo.catalog_sales (cs_sold_date_sk);
    CREATE NONCLUSTERED INDEX FINDINGS_web_date ON dbo.web_sales (ws_sold_date_sk);
END;

IF @scenario IN (N'columnstore', N'columnstore_date_keys')
BEGIN
    -- All columns of these numeric TPC-DS fact tables, not an arbitrary subset.
    -- Existing clustered primary keys are retained.
    DECLARE @columns nvarchar(max);
    DECLARE fact_tables CURSOR LOCAL FAST_FORWARD FOR
        SELECT name FROM sys.tables WHERE schema_id = SCHEMA_ID(N'dbo')
          AND name IN (N'store_sales', N'catalog_sales', N'web_sales') ORDER BY name;
    OPEN fact_tables;
    FETCH NEXT FROM fact_tables INTO @table;
    WHILE @@FETCH_STATUS = 0
    BEGIN
        SELECT @columns = STRING_AGG(CONVERT(nvarchar(max), QUOTENAME(name)), N',')
            WITHIN GROUP (ORDER BY column_id)
        FROM sys.columns WHERE object_id = OBJECT_ID(N'dbo.' + @table);
        SET @name = CASE @table WHEN N'store_sales' THEN N'FINDINGS_store_columnstore'
            WHEN N'catalog_sales' THEN N'FINDINGS_catalog_columnstore' ELSE N'FINDINGS_web_columnstore' END;
        SET @sql = N'CREATE NONCLUSTERED COLUMNSTORE INDEX ' + QUOTENAME(@name)
            + N' ON dbo.' + QUOTENAME(@table) + N' (' + @columns + N');';
        EXEC sys.sp_executesql @sql;
        FETCH NEXT FROM fact_tables INTO @table;
    END;
    CLOSE fact_tables;
    DEALLOCATE fact_tables;
END;

-- The runner normalizes statistics with FULLSCAN after plan compilation for
-- every scenario, including baseline. Index setup time is recorded separately.
