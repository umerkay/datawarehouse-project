SELECT a.i_item_sk
FROM item a JOIN item b ON a.i_item_sk < b.i_item_sk;
