--
-- Legal Notice 
-- 
-- This document and associated source code (the "Work") is a part of a 
-- benchmark specification maintained by the TPC. 
-- 
-- The TPC reserves all right, title, and interest to the Work as provided 
-- under U.S. and international laws, including without limitation all patent 
-- and trademark rights therein. 
-- 
-- No Warranty 
-- 
-- 1.1 TO THE MAXIMUM EXTENT PERMITTED BY APPLICABLE LAW, THE INFORMATION 
--     CONTAINED HEREIN IS PROVIDED "AS IS" AND WITH ALL FAULTS, AND THE 
--     AUTHORS AND DEVELOPERS OF THE WORK HEREBY DISCLAIM ALL OTHER 
--     WARRANTIES AND CONDITIONS, EITHER EXPRESS, IMPLIED OR STATUTORY, 
--     INCLUDING, BUT NOT LIMITED TO, ANY (IF ANY) IMPLIED WARRANTIES, 
--     DUTIES OR CONDITIONS OF MERCHANTABILITY, OF FITNESS FOR A PARTICULAR 
--     PURPOSE, OF ACCURACY OR COMPLETENESS OF RESPONSES, OF RESULTS, OF 
--     WORKMANLIKE EFFORT, OF LACK OF VIRUSES, AND OF LACK OF NEGLIGENCE. 
--     ALSO, THERE IS NO WARRANTY OR CONDITION OF TITLE, QUIET ENJOYMENT, 
--     QUIET POSSESSION, CORRESPONDENCE TO DESCRIPTION OR NON-INFRINGEMENT 
--     WITH REGARD TO THE WORK. 
-- 1.2 IN NO EVENT WILL ANY AUTHOR OR DEVELOPER OF THE WORK BE LIABLE TO 
--     ANY OTHER PARTY FOR ANY DAMAGES, INCLUDING BUT NOT LIMITED TO THE 
--     COST OF PROCURING SUBSTITUTE GOODS OR SERVICES, LOST PROFITS, LOSS 
--     OF USE, LOSS OF DATA, OR ANY INCIDENTAL, CONSEQUENTIAL, DIRECT, 
--     INDIRECT, OR SPECIAL DAMAGES WHETHER UNDER CONTRACT, TORT, WARRANTY,
--     OR OTHERWISE, ARISING IN ANY WAY OUT OF THIS OR ANY OTHER AGREEMENT 
--     RELATING TO THE WORK, WHETHER OR NOT SUCH AUTHOR OR DEVELOPER HAD 
--     ADVANCE NOTICE OF THE POSSIBILITY OF SUCH DAMAGES. 
-- 
-- Contributors:
-- 
define YEAR=random(1998,2001,uniform);
define SELECTONE= text({"c_preferred_cust_flag",1}
                       ,{"c_birth_country",1}
                       ,{"c_login",1}
                       ,{"c_email_address",1});
define _LIMIT=100;

WITH store_year AS (
	SELECT c_customer_id customer_id
	       ,c_first_name customer_first_name
	       ,c_last_name customer_last_name
	       ,[SELECTONE]
	       ,d_year dyear
	       ,sum(((ss_ext_list_price-ss_ext_wholesale_cost-ss_ext_discount_amt)+ss_ext_sales_price)/2) year_total
	FROM customer 
		INNER JOIN store_sales ON c_customer_sk = ss_customer_sk
		INNER JOIN date_dim ON ss_sold_date_sk = d_date_sk
	WHERE d_year IN ([YEAR], [YEAR]+1)
	GROUP BY c_customer_id
	         ,c_first_name
	         ,c_last_name
	         ,c_preferred_cust_flag
	         ,c_birth_country
	         ,c_login
	         ,c_email_address
	         ,d_year
), store_two_years AS (
	SELECT
		  t_s_secyear.customer_id
	     ,t_s_secyear.customer_first_name
	     ,t_s_secyear.customer_last_name
	     ,t_s_secyear.[SELECTONE]
	     ,t_s_secyear.year_total / t_s_firstyear.year_total year_total_ratio
	FROM store_year t_s_firstyear INNER JOIN store_year t_s_secyear ON t_s_secyear.customer_id = t_s_firstyear.customer_id
	WHERE t_s_firstyear.dyear =  [YEAR]
	   and t_s_secyear.dyear = [YEAR]+1
	   and t_s_firstyear.year_total > 0
), catalog_year AS (
	select c_customer_id customer_id
	       ,c_first_name customer_first_name
	       ,c_last_name customer_last_name
	       ,[SELECTONE]
	       ,d_year dyear
       	   ,sum((((cs_ext_list_price-cs_ext_wholesale_cost-cs_ext_discount_amt)+cs_ext_sales_price)/2) ) year_total
	from customer
		INNER JOIN catalog_sales ON c_customer_sk = cs_bill_customer_sk
		INNER JOIN date_dim ON cs_sold_date_sk = d_date_sk
	WHERE d_year IN ([YEAR], [YEAR]+1)
 	group by c_customer_id
         ,c_first_name
         ,c_last_name
         ,c_preferred_cust_flag
         ,c_birth_country
         ,c_login
         ,c_email_address
         ,d_year
), catalog_two_years AS (
	SELECT 
		  t_s_secyear.customer_id
	     ,t_s_secyear.customer_first_name
	     ,t_s_secyear.customer_last_name
	     ,t_s_secyear.[SELECTONE]
	     ,t_s_secyear.year_total / t_s_firstyear.year_total year_total_ratio
	FROM catalog_year t_s_firstyear INNER JOIN catalog_year t_s_secyear ON t_s_secyear.customer_id = t_s_firstyear.customer_id
	WHERE t_s_firstyear.dyear =  [YEAR]
	   and t_s_secyear.dyear = [YEAR]+1
	   and t_s_firstyear.year_total > 0
), web_year AS (
	SELECT c_customer_id customer_id
	       ,c_first_name customer_first_name
	       ,c_last_name customer_last_name
	       ,[SELECTONE]
	       ,d_year dyear
	       ,SUM((ws_ext_list_price - ws_ext_wholesale_cost - ws_ext_discount_amt + ws_ext_sales_price) / 2.0) year_total
	FROM customer
	     INNER JOIN web_sales ON c_customer_sk = ws_bill_customer_sk
	     INNER JOIN date_dim ON ws_sold_date_sk = d_date_sk
	WHERE d_year IN ([YEAR], [YEAR]+1)
	GROUP BY c_customer_id
	         ,c_first_name
	         ,c_last_name
	         ,c_preferred_cust_flag
	         ,c_birth_country
	         ,c_login
	         ,c_email_address
	         ,d_year
), web_two_years AS (
	SELECT
		  t_s_secyear.customer_id
	     ,t_s_secyear.customer_first_name
	     ,t_s_secyear.customer_last_name
	     ,t_s_secyear.[SELECTONE]
	     ,t_s_secyear.year_total / t_s_firstyear.year_total year_total_ratio
	 FROM web_year t_s_firstyear INNER JOIN web_year t_s_secyear ON t_s_firstyear.customer_id = t_s_secyear.customer_id
     WHERE t_s_firstyear.dyear =  [YEAR]
	   and t_s_secyear.dyear = [YEAR]+1
	   and t_s_firstyear.year_total > 0
)
[_LIMITA] SELECT [_LIMITB] 
      t_s.customer_id
     ,t_s.customer_first_name
     ,t_s.customer_last_name
     ,t_s.[SELECTONE]
FROM store_two_years t_s
	INNER JOIN catalog_two_years t_c ON t_s.customer_id = t_c.customer_id
	INNER JOIN web_two_years t_w ON t_s.customer_id = t_w.customer_id
WHERE t_c.year_total_ratio > t_s.year_total_ratio
	AND t_c.year_total_ratio > t_w.year_total_ratio
ORDER BY t_s.customer_id
     ,t_s.customer_first_name
     ,t_s.customer_last_name
     ,t_s.[SELECTONE]
[_LIMITC];

