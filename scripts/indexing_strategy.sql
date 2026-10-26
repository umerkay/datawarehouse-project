/*
    TPC-DS analytical indexing strategy for SQL Server 2022.

    Keep the schema's clustered primary keys, add a covering nonclustered
    columnstore index to every fact table, and add only the selective rowstore
    indexes used by the generated workload. Fact statistics use a bounded
    sample so preparation time remains practical as the scale factor grows.

    Versioned names make this script convergent: rerunning it does not rebuild
    healthy indexes. Known partial v1 columnstore indexes are removed only after
    their v2 replacements exist.
*/
SET NOCOUNT ON;
SET XACT_ABORT ON;

DECLARE @FactTables table (table_name sysname PRIMARY KEY);
INSERT @FactTables (table_name) VALUES
    (N'store_sales'), (N'store_returns'),
    (N'catalog_sales'), (N'catalog_returns'),
    (N'web_sales'), (N'web_returns'),
    (N'inventory');

DECLARE
    @table_name sysname,
    @object_id int,
    @index_name sysname,
    @columns nvarchar(max),
    @sql nvarchar(max);

DECLARE fact_cursor CURSOR LOCAL FAST_FORWARD FOR
    SELECT table_name FROM @FactTables ORDER BY table_name;

OPEN fact_cursor;
FETCH NEXT FROM fact_cursor INTO @table_name;
WHILE @@FETCH_STATUS = 0
BEGIN
    SET @object_id = OBJECT_ID(QUOTENAME(N'dbo') + N'.' + QUOTENAME(@table_name));
    IF @object_id IS NULL
        THROW 50001, 'A required TPC-DS fact table is missing.', 1;

    SET @index_name = N'NCCI_' + @table_name + N'_analytics_v2';

    IF EXISTS (
        SELECT 1 FROM sys.indexes
        WHERE object_id = @object_id AND name = @index_name AND is_disabled = 1
    )
    BEGIN
        SET @sql = N'ALTER INDEX ' + QUOTENAME(@index_name) + N' ON dbo.'
                 + QUOTENAME(@table_name) + N' REBUILD;';
        PRINT @sql;
        EXEC sys.sp_executesql @sql;
    END
    ELSE IF NOT EXISTS (
        SELECT 1 FROM sys.indexes
        WHERE object_id = @object_id AND name = @index_name
    )
    BEGIN
        SELECT @columns = STRING_AGG(
            CONVERT(nvarchar(max), QUOTENAME(c.name)), N', '
        ) WITHIN GROUP (ORDER BY c.column_id)
        FROM sys.columns AS c
        WHERE c.object_id = @object_id;

        SET @sql = N'CREATE NONCLUSTERED COLUMNSTORE INDEX '
                 + QUOTENAME(@index_name) + N' ON dbo.' + QUOTENAME(@table_name)
                 + N' (' + @columns + N');';
        PRINT N'Creating ' + @index_name;
        EXEC sys.sp_executesql @sql;
    END;

    FETCH NEXT FROM fact_cursor INTO @table_name;
END;
CLOSE fact_cursor;
DEALLOCATE fact_cursor;

/* Selective dimension and anti-join access paths. */
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID(N'dbo.date_dim') AND name = N'IX_date_dim_workload_v2')
    CREATE INDEX IX_date_dim_workload_v2 ON dbo.date_dim
        (d_year, d_week_seq, d_moy, d_dom, d_date_sk)
        INCLUDE (d_qoy, d_month_seq, d_date);

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID(N'dbo.item') AND name = N'IX_item_product_hierarchy_v2')
    CREATE INDEX IX_item_product_hierarchy_v2 ON dbo.item
        (i_brand_id, i_class_id, i_category_id, i_item_sk)
        INCLUDE (i_manufact_id, i_manufact, i_category, i_color, i_units, i_size,
                 i_item_id, i_product_name, i_item_desc, i_current_price);

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID(N'dbo.customer') AND name = N'IX_customer_address_v2')
    CREATE INDEX IX_customer_address_v2 ON dbo.customer
        (c_current_addr_sk, c_customer_sk)
        INCLUDE (c_current_cdemo_sk, c_current_hdemo_sk);

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID(N'dbo.customer_address') AND name = N'IX_customer_address_geography_v2')
    CREATE INDEX IX_customer_address_geography_v2 ON dbo.customer_address
        (ca_state, ca_zip, ca_county, ca_address_sk)
        INCLUDE (ca_country, ca_gmt_offset);

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID(N'dbo.store_returns') AND name = N'IX_store_returns_antijoin_v2')
    CREATE INDEX IX_store_returns_antijoin_v2 ON dbo.store_returns
        (sr_ticket_number, sr_item_sk)
        INCLUDE (sr_customer_sk, sr_returned_date_sk, sr_net_loss);

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID(N'dbo.catalog_returns') AND name = N'IX_catalog_returns_antijoin_v2')
    CREATE INDEX IX_catalog_returns_antijoin_v2 ON dbo.catalog_returns
        (cr_order_number, cr_item_sk)
        INCLUDE (cr_refunded_customer_sk, cr_returned_date_sk, cr_net_loss);

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID(N'dbo.web_returns') AND name = N'IX_web_returns_antijoin_v2')
    CREATE INDEX IX_web_returns_antijoin_v2 ON dbo.web_returns
        (wr_order_number, wr_item_sk)
        INCLUDE (wr_refunded_customer_sk, wr_returned_date_sk, wr_net_loss);

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID(N'dbo.inventory') AND name = N'IX_inventory_item_date_v2')
    CREATE INDEX IX_inventory_item_date_v2 ON dbo.inventory
        (inv_item_sk, inv_date_sk, inv_warehouse_sk)
        INCLUDE (inv_quantity_on_hand);

/* Drop only the known partial v1 columnstore indexes after v2 is available. */
DECLARE legacy_cursor CURSOR LOCAL FAST_FORWARD FOR
    SELECT table_name FROM @FactTables;
OPEN legacy_cursor;
FETCH NEXT FROM legacy_cursor INTO @table_name;
WHILE @@FETCH_STATUS = 0
BEGIN
    SET @object_id = OBJECT_ID(QUOTENAME(N'dbo') + N'.' + QUOTENAME(@table_name));
    SET @index_name = N'NCCI_' + @table_name;
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = @object_id AND name = @index_name)
    BEGIN
        SET @sql = N'DROP INDEX ' + QUOTENAME(@index_name) + N' ON dbo.' + QUOTENAME(@table_name) + N';';
        PRINT N'Removing superseded ' + @index_name;
        EXEC sys.sp_executesql @sql;
    END;
    FETCH NEXT FROM legacy_cursor INTO @table_name;
END;
CLOSE legacy_cursor;
DEALLOCATE legacy_cursor;

/* Statistics policy: accurate small dimensions, bounded work on growing facts. */
UPDATE STATISTICS dbo.date_dim WITH FULLSCAN;
UPDATE STATISTICS dbo.item WITH FULLSCAN;
UPDATE STATISTICS dbo.customer WITH FULLSCAN;
UPDATE STATISTICS dbo.customer_address WITH FULLSCAN;

DECLARE stats_cursor CURSOR LOCAL FAST_FORWARD FOR
    SELECT table_name FROM @FactTables;
OPEN stats_cursor;
FETCH NEXT FROM stats_cursor INTO @table_name;
WHILE @@FETCH_STATUS = 0
BEGIN
    SET @sql = N'UPDATE STATISTICS dbo.' + QUOTENAME(@table_name) + N' WITH SAMPLE 10 PERCENT;';
    PRINT N'Updating sampled statistics on ' + @table_name;
    EXEC sys.sp_executesql @sql;
    FETCH NEXT FROM stats_cursor INTO @table_name;
END;
CLOSE stats_cursor;
DEALLOCATE stats_cursor;

PRINT 'TPC-DS analytical indexing strategy v2 applied successfully.';
