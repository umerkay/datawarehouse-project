/* Validated scale-aware helper indexes. Invoke only for SF5/SF10. */
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'dbo.date_dim') AND name=N'TPCDS_IX_date_dim_filters')
    CREATE INDEX TPCDS_IX_date_dim_filters ON dbo.date_dim(d_year,d_moy,d_dom,d_week_seq,d_date_sk)
        INCLUDE(d_date,d_month_seq,d_qoy);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'dbo.item') AND name=N'TPCDS_IX_item_filters')
    CREATE INDEX TPCDS_IX_item_filters ON dbo.item(i_manufact_id,i_manager_id,i_category_id,i_class_id,i_brand_id,i_item_sk)
        INCLUDE(i_current_price,i_color,i_units,i_size);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'dbo.customer') AND name=N'TPCDS_IX_customer_address')
    CREATE INDEX TPCDS_IX_customer_address ON dbo.customer(c_current_addr_sk,c_customer_sk)
        INCLUDE(c_current_cdemo_sk,c_current_hdemo_sk);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'dbo.customer_address') AND name=N'TPCDS_IX_address_geo')
    CREATE INDEX TPCDS_IX_address_geo ON dbo.customer_address(ca_state,ca_zip,ca_county,ca_address_sk)
        INCLUDE(ca_country,ca_city,ca_gmt_offset);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'dbo.customer_demographics') AND name=N'TPCDS_IX_demographic_filters')
    CREATE INDEX TPCDS_IX_demographic_filters ON dbo.customer_demographics(cd_gender,cd_marital_status,cd_education_status,cd_demo_sk);
