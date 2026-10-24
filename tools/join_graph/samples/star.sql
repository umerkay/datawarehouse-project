SELECT ss.ss_item_sk
FROM store_sales ss, item i, date_dim d
WHERE ss.ss_item_sk = i.i_item_sk
  AND ss.ss_sold_date_sk = d.d_date_sk;
