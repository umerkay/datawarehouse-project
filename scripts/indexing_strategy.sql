SET NOCOUNT ON;
SET XACT_ABORT ON;

-- Selected from the dimension-index experiment: single-column B-tree indexes.

-- Clear existing indexes to make applying the strategy reload all strategy components.
DROP INDEX IF EXISTS IX_customer_current_addr ON dbo.customer;
DROP INDEX IF EXISTS IX_customer_address_state ON dbo.customer_address;
DROP INDEX IF EXISTS IX_date_dim_year ON dbo.date_dim;
DROP INDEX IF EXISTS IX_date_dim_moy ON dbo.date_dim;
DROP INDEX IF EXISTS IX_date_dim_date ON dbo.date_dim;
DROP INDEX IF EXISTS IX_date_dim_month_seq ON dbo.date_dim;

-- Customer is most joined on c_current_addr_sk (19 times) which is not the primary key
CREATE NONCLUSTERED INDEX IX_customer_current_addr ON dbo.customer(c_current_addr_sk);

-- Customer address is mostly filtered by ca_state (11 times)
CREATE NONCLUSTERED INDEX IX_customer_address_state ON dbo.customer_address(ca_state);

-- Date dimension is mostly filtered by d_year (54 times), d_moy (24 times), d_date (17 times) and d_month_seq (16 times)
CREATE NONCLUSTERED INDEX IX_date_dim_year ON dbo.date_dim(d_year);

CREATE NONCLUSTERED INDEX IX_date_dim_moy ON dbo.date_dim(d_moy);

CREATE NONCLUSTERED INDEX IX_date_dim_date ON dbo.date_dim(d_date);

CREATE NONCLUSTERED INDEX IX_date_dim_month_seq ON dbo.date_dim(d_month_seq);
GO

-- Clear materialized views if they existed previously, so they are reconstructed.
DROP VIEW IF EXISTS dbo.MV_Web_Sales_Daily_Item;
GO

DROP VIEW IF EXISTS dbo.MV_Store_Sales_Daily_Item;
GO

-- Required SET options for creating indexed views
SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;
GO
--

CREATE VIEW dbo.MV_Web_Sales_Daily_Item
WITH SCHEMABINDING AS
SELECT 
    ws_sold_date_sk, 
    ws_item_sk, 
    SUM(ISNULL(ws_quantity * ws_list_price, 0)) AS sum_qty_list_price,
    COUNT_BIG(*) AS cnt
FROM dbo.web_sales
GROUP BY 
    ws_sold_date_sk, 
    ws_item_sk
GO

CREATE UNIQUE CLUSTERED INDEX CIX_MV_Web_Sales_Daily_Item
ON dbo.MV_Web_Sales_Daily_Item (ws_sold_date_sk, ws_item_sk);
GO

CREATE VIEW dbo.MV_Store_Sales_Daily_Item
WITH SCHEMABINDING AS
SELECT 
    ss_sold_date_sk, 
    ss_item_sk, 
    ss_store_sk,
    SUM(isnull(ss_quantity*ss_sales_price,0)) AS sum_qty_sales_price,
    SUM(isnull(ss_quantity*ss_list_price,0)) AS sum_qty_list_price,
    SUM(isnull(ss_net_profit,0)) AS sum_net_profit,
    COUNT_BIG(*) AS cnt
FROM dbo.store_sales
GROUP BY 
    ss_sold_date_sk, 
    ss_item_sk, 
    ss_store_sk;
GO

CREATE UNIQUE CLUSTERED INDEX CIX_MV_Store_Sales_Daily_Item
ON dbo.MV_Store_Sales_Daily_Item (ss_sold_date_sk, ss_item_sk, ss_store_sk);