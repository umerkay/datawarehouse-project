/* Additional B-tree indexes only; all phases retain the same seven CCIs. */
SET NOCOUNT ON;
SET XACT_ABORT ON;
IF DB_NAME() <> N'$(EXPERIMENT_DB)' OR CHARINDEX(N'_findings_', DB_NAME()) = 0
    THROW 51000, 'Findings indexes require the private experiment database', 1;
DECLARE @scenario sysname=N'$(SCENARIO)';
IF @scenario NOT IN (N'baseline',N'date_keys',N'date_shared')
    THROW 51000, 'Unknown columnstore-base scenario', 1;
IF (SELECT COUNT(*) FROM sys.indexes i JOIN sys.tables t ON t.object_id=i.object_id
    WHERE t.schema_id=SCHEMA_ID(N'dbo') AND i.type=5 AND i.is_disabled=0 AND t.name IN
    (N'store_sales',N'catalog_sales',N'web_sales',N'store_returns',N'catalog_returns',N'web_returns',N'inventory')) <> 7
    THROW 51000, 'Prepare all seven clustered columnstores before scenarios', 1;
DECLARE @sql nvarchar(max), @name sysname, @table sysname;
DECLARE old_indexes CURSOR LOCAL FAST_FORWARD FOR
SELECT i.name,t.name FROM sys.indexes i JOIN sys.tables t ON t.object_id=i.object_id
WHERE t.schema_id=SCHEMA_ID(N'dbo') AND
    ((t.name=N'store_sales' AND i.name=N'FINDINGS_store_date') OR
     (t.name=N'catalog_sales' AND i.name=N'FINDINGS_catalog_date') OR
     (t.name=N'web_sales' AND i.name=N'FINDINGS_web_date'));
OPEN old_indexes;
FETCH NEXT FROM old_indexes INTO @name,@table;
WHILE @@FETCH_STATUS=0
BEGIN
    SET @sql=N'DROP INDEX '+QUOTENAME(@name)+N' ON dbo.'+QUOTENAME(@table);
    EXEC sys.sp_executesql @sql;
    FETCH NEXT FROM old_indexes INTO @name,@table;
END;
CLOSE old_indexes;
DEALLOCATE old_indexes;
IF @scenario IN (N'date_keys',N'date_shared')
BEGIN
    SET @sql=N'CREATE NONCLUSTERED INDEX FINDINGS_store_date ON dbo.store_sales (ss_sold_date_sk)';
    -- CCI has no implicit clustered rowstore key in this secondary index.
    -- Include item explicitly to cover query_006's sales-side joins.
    IF @scenario=N'date_shared' SET @sql+=N' INCLUDE (ss_item_sk,ss_customer_sk,ss_quantity,ss_sales_price)';
    EXEC sys.sp_executesql @sql;
    CREATE NONCLUSTERED INDEX FINDINGS_catalog_date ON dbo.catalog_sales (cs_sold_date_sk);
    CREATE NONCLUSTERED INDEX FINDINGS_web_date ON dbo.web_sales (ws_sold_date_sk);
END;
