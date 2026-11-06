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
define COUNTY = random(1, rowcount("active_counties", "store"), uniform);
define STATE = distmember(fips_county, [COUNTY], 3); 
define YEAR = random(1998, 2002, uniform);
define AGG_FIELD = text({"SR_RETURN_AMT",1},{"SR_FEE",1},{"SR_REFUNDED_CASH",1},{"SR_RETURN_AMT_INC_TAX",1},{"SR_REVERSED_CHARGE",1},{"SR_STORE_CREDIT",1},{"SR_RETURN_TAX",1});
define _LIMIT=100;

WITH customer_total_return AS (
    SELECT sr_customer_sk AS ctr_customer_sk,
           sr_store_sk AS ctr_store_sk,
           SUM([AGG_FIELD]) AS ctr_total_return
    FROM store_returns
    INNER JOIN date_dim ON sr_returned_date_sk = d_date_sk
    WHERE d_year = [YEAR]
    GROUP BY sr_customer_sk, sr_store_sk
),
store_threshold AS (
    SELECT ctr_store_sk,
           AVG(ctr_total_return) * 1.2 AS threshold_limit
    FROM customer_total_return
    GROUP BY ctr_store_sk
)
[_LIMITA] SELECT [_LIMITB] c_customer_id
FROM customer_total_return ctr1
INNER JOIN store_threshold st ON ctr1.ctr_store_sk = st.ctr_store_sk
INNER JOIN store ON s_store_sk = ctr1.ctr_store_sk
INNER JOIN customer ON ctr1.ctr_customer_sk = c_customer_sk
WHERE ctr1.ctr_total_return > st.threshold_limit
  AND s_state = '[STATE]'
ORDER BY c_customer_id
[_LIMITC];
