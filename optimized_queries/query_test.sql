SELECT 
    c.c_customer_sk,
    i.i_item_id,
    SUM(ss.ss_quantity * ss.ss_sales_price) as total_sales,
    AVG(ss.ss_net_profit) as avg_profit
FROM 
    store_sales ss
JOIN 
    customer c ON ss.ss_customer_sk = c.c_customer_sk
JOIN 
    item i ON ss.ss_item_sk = i.i_item_sk
GROUP BY 
    c.c_customer_sk,
    i.i_item_id
ORDER BY 
    total_sales DESC;