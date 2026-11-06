DROP VIEW IF EXISTS dbo.MV_Store_Sales_Daily_Item;
GO

DROP VIEW IF EXISTS dbo.MV_Web_Sales_Daily_Item;
GO

-- Required SET options for creating indexed views
SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;
GO
--

CREATE VIEW dbo.MV_Store_Sales_Daily_Item
WITH SCHEMABINDING AS
SELECT 
    ss_sold_date_sk, 
    ss_item_sk, 
    ss_store_sk,
    ss_addr_sk,
    SUM(isnull(ss_ext_sales_price,0)) AS sum_ext_sales_price,
    SUM(isnull(ss_sales_price*ss_quantity,0)) AS sum_sales_price_times_quantity,
    SUM(isnull(ss_net_profit,0)) AS sum_net_profit,
    COUNT_BIG(*) AS cnt
FROM dbo.store_sales
GROUP BY 
    ss_sold_date_sk, 
    ss_item_sk, 
    ss_store_sk,
    ss_addr_sk;
GO

CREATE UNIQUE CLUSTERED INDEX CIX_MV_Store_Sales_Daily_Item
ON dbo.MV_Store_Sales_Daily_Item (ss_sold_date_sk, ss_item_sk, ss_store_sk, ss_addr_sk);
GO

CREATE VIEW dbo.MV_Web_Sales_Daily_Item
WITH SCHEMABINDING AS
SELECT 
    ws_sold_date_sk, 
    ws_item_sk, 
    ws_bill_customer_sk,
    SUM(ISNULL(ws_quantity * ws_list_price, 0)) AS sum_qty_list_price,
    SUM(ISNULL(ws_ext_sales_price,0)) AS sum_ext_sales_price,
    SUM(ISNULL(ws_ext_list_price, 0)) AS sum_ext_list_price,
    SUM(ISNULL(ws_ext_wholesale_cost, 0)) AS sum_ext_wholesale_cost,
    SUM(ISNULL(ws_ext_discount_amt, 0)) AS sum_ext_discount_amt,
    COUNT_BIG(*) AS cnt
FROM dbo.web_sales
WHERE ws_item_sk IS NOT NULL
GROUP BY 
    ws_sold_date_sk, 
    ws_item_sk, 
    ws_bill_customer_sk;
GO

CREATE UNIQUE CLUSTERED INDEX CIX_MV_Web_Sales_Daily_Item
ON dbo.MV_Web_Sales_Daily_Item (ws_sold_date_sk, ws_item_sk, ws_bill_customer_sk);