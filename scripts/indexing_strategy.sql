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

    IF NOT EXISTS (
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

        PRINT @sql;
        EXEC sys.sp_executesql @sql;
    END;

    FETCH NEXT FROM fact_cursor INTO @table_name;
END;

CLOSE fact_cursor;
DEALLOCATE fact_cursor;
