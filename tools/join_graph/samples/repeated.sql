SELECT ss.ss_item_sk
FROM item i JOIN store_sales ss ON i.i_item_sk = ss.ss_item_sk
WHERE ss.ss_item_sk = i.i_item_sk;
