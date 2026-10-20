/*
    Final analytical indexing strategy for the TPC-DS SQL Server workload.

    Apply after bulk loading and referential-integrity creation. The clustered
    primary keys created by the schema prevent clustered columnstore indexes,
    so nonclustered columnstore indexes target the large fact tables. The
    nonclustered rowstore indexes support selective dimension joins.
*/
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF NOT EXISTS (
    SELECT 1
    FROM sys.indexes
    WHERE name = N'NCCI_store_sales'
      AND object_id = OBJECT_ID(N'dbo.store_sales')
)
BEGIN
    PRINT 'Creating NCCI_store_sales';
    CREATE NONCLUSTERED COLUMNSTORE INDEX NCCI_store_sales
        ON dbo.store_sales (
            ss_sold_date_sk, ss_sold_time_sk, ss_item_sk, ss_customer_sk,
            ss_store_sk, ss_ticket_number, ss_quantity, ss_list_price,
            ss_sales_price, ss_ext_sales_price, ss_ext_list_price,
            ss_ext_wholesale_cost, ss_ext_discount_amt, ss_net_paid,
            ss_net_paid_inc_tax, ss_net_profit
        );
END;
GO

IF NOT EXISTS (
    SELECT 1
    FROM sys.indexes
    WHERE name = N'NCCI_catalog_sales'
      AND object_id = OBJECT_ID(N'dbo.catalog_sales')
)
BEGIN
    PRINT 'Creating NCCI_catalog_sales';
    CREATE NONCLUSTERED COLUMNSTORE INDEX NCCI_catalog_sales
        ON dbo.catalog_sales (
            cs_sold_date_sk, cs_sold_time_sk, cs_item_sk,
            cs_bill_customer_sk, cs_ship_customer_sk, cs_quantity,
            cs_list_price, cs_sales_price, cs_ext_sales_price,
            cs_ext_list_price, cs_ext_wholesale_cost, cs_ext_discount_amt,
            cs_net_profit
        );
END;
GO

IF NOT EXISTS (
    SELECT 1
    FROM sys.indexes
    WHERE name = N'NCCI_web_sales'
      AND object_id = OBJECT_ID(N'dbo.web_sales')
)
BEGIN
    PRINT 'Creating NCCI_web_sales';
    CREATE NONCLUSTERED COLUMNSTORE INDEX NCCI_web_sales
        ON dbo.web_sales (
            ws_sold_date_sk, ws_sold_time_sk, ws_item_sk,
            ws_bill_customer_sk, ws_ship_customer_sk, ws_quantity,
            ws_list_price, ws_sales_price, ws_ext_sales_price,
            ws_ext_list_price, ws_ext_wholesale_cost, ws_ext_discount_amt,
            ws_net_profit
        );
END;
GO

IF NOT EXISTS (
    SELECT 1
    FROM sys.indexes
    WHERE name = N'NCCI_store_returns'
      AND object_id = OBJECT_ID(N'dbo.store_returns')
)
BEGIN
    PRINT 'Creating NCCI_store_returns';
    CREATE NONCLUSTERED COLUMNSTORE INDEX NCCI_store_returns
        ON dbo.store_returns (
            sr_returned_date_sk, sr_item_sk, sr_customer_sk,
            sr_store_sk, sr_ticket_number, sr_net_loss
        );
END;
GO

IF NOT EXISTS (
    SELECT 1
    FROM sys.indexes
    WHERE name = N'IX_date_dim_year_month_quarter'
      AND object_id = OBJECT_ID(N'dbo.date_dim')
)
BEGIN
    PRINT 'Creating IX_date_dim_year_month_quarter';
    CREATE INDEX IX_date_dim_year_month_quarter
        ON dbo.date_dim (d_year, d_moy, d_qoy, d_date_sk)
        INCLUDE (d_date, d_month_seq);
END;
GO

IF NOT EXISTS (
    SELECT 1
    FROM sys.indexes
    WHERE name = N'IX_customer_current_address'
      AND object_id = OBJECT_ID(N'dbo.customer')
)
BEGIN
    PRINT 'Creating IX_customer_current_address';
    CREATE INDEX IX_customer_current_address
        ON dbo.customer (c_current_addr_sk, c_customer_sk)
        INCLUDE (c_current_cdemo_sk);
END;
GO

IF NOT EXISTS (
    SELECT 1
    FROM sys.indexes
    WHERE name = N'IX_customer_address_state_zip_county'
      AND object_id = OBJECT_ID(N'dbo.customer_address')
)
BEGIN
    PRINT 'Creating IX_customer_address_state_zip_county';
    CREATE INDEX IX_customer_address_state_zip_county
        ON dbo.customer_address (ca_state, ca_zip, ca_county, ca_address_sk)
        INCLUDE (ca_country);
END;
GO

IF NOT EXISTS (
    SELECT 1
    FROM sys.indexes
    WHERE name = N'IX_item_manufacturer_attributes'
      AND object_id = OBJECT_ID(N'dbo.item')
)
BEGIN
    PRINT 'Creating IX_item_manufacturer_attributes';
    CREATE INDEX IX_item_manufacturer_attributes
        ON dbo.item (i_manufact_id, i_manufact, i_category, i_color, i_units, i_size)
        INCLUDE (i_item_id, i_product_name, i_item_desc, i_current_price);
END;
GO

IF NOT EXISTS (
    SELECT 1
    FROM sys.indexes
    WHERE name = N'IX_inventory_item_date'
      AND object_id = OBJECT_ID(N'dbo.inventory')
)
BEGIN
    PRINT 'Creating IX_inventory_item_date';
    CREATE INDEX IX_inventory_item_date
        ON dbo.inventory (inv_item_sk, inv_date_sk)
        INCLUDE (inv_quantity_on_hand);
END;
GO

IF NOT EXISTS (
    SELECT 1
    FROM sys.indexes
    WHERE name = N'IX_store_returns_ticket_item_customer'
      AND object_id = OBJECT_ID(N'dbo.store_returns')
)
BEGIN
    PRINT 'Creating IX_store_returns_ticket_item_customer';
    CREATE INDEX IX_store_returns_ticket_item_customer
        ON dbo.store_returns (sr_ticket_number, sr_item_sk, sr_customer_sk)
        INCLUDE (sr_returned_date_sk, sr_net_loss);
END;
GO

PRINT 'Updating statistics for the indexed workload';
UPDATE STATISTICS dbo.store_sales WITH FULLSCAN;
UPDATE STATISTICS dbo.catalog_sales WITH FULLSCAN;
UPDATE STATISTICS dbo.web_sales WITH FULLSCAN;
UPDATE STATISTICS dbo.store_returns WITH FULLSCAN;
UPDATE STATISTICS dbo.date_dim WITH FULLSCAN;
UPDATE STATISTICS dbo.customer WITH FULLSCAN;
UPDATE STATISTICS dbo.customer_address WITH FULLSCAN;
UPDATE STATISTICS dbo.item WITH FULLSCAN;
UPDATE STATISTICS dbo.inventory WITH FULLSCAN;
GO

PRINT 'Indexing strategy applied successfully';
