-- Run in the database whose plans you captured. Export results as UTF-8 CSV
-- with a header, using your SQL client (one row per index column).
SELECT QUOTENAME(s.name) AS [schema], QUOTENAME(t.name) AS [table],
       i.name AS index_name, i.type_desc AS index_type,
       COALESCE(i.filter_definition, '') AS filter_definition,
       QUOTENAME(c.name) AS column_name, ic.key_ordinal,
       CAST(ic.is_included_column AS int) AS is_included_column
FROM sys.indexes i
JOIN sys.tables t ON t.object_id = i.object_id
JOIN sys.schemas s ON s.schema_id = t.schema_id
JOIN sys.index_columns ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id
JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
WHERE i.is_disabled = 0 AND i.is_hypothetical = 0 AND i.index_id > 0
ORDER BY s.name, t.name, i.name, ic.key_ordinal, ic.index_column_id;
