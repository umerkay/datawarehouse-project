-- Only the nine requested single-column dimension indexes. Facts stay unchanged.
SET NOCOUNT ON;
SET XACT_ABORT ON;
IF DB_NAME() <> N'$(EXPERIMENT_DB)' OR CHARINDEX(N'_dimexp_', DB_NAME()) = 0
    THROW 51000, 'Use only the private dimension experiment database', 1;
DECLARE @scenario sysname = N'$(SCENARIO)';
IF @scenario NOT IN (N'baseline',N'all_dimensions')
    THROW 51000, 'Unknown dimension scenario', 1;

DROP INDEX IF EXISTS DIMEXP_customer_addr ON dbo.customer;
DROP INDEX IF EXISTS DIMEXP_address_state ON dbo.customer_address;
DROP INDEX IF EXISTS DIMEXP_address_gmt ON dbo.customer_address;
DROP INDEX IF EXISTS DIMEXP_demo_education ON dbo.customer_demographics;
DROP INDEX IF EXISTS DIMEXP_demo_marital ON dbo.customer_demographics;
DROP INDEX IF EXISTS DIMEXP_date_year ON dbo.date_dim;
DROP INDEX IF EXISTS DIMEXP_date_moy ON dbo.date_dim;
DROP INDEX IF EXISTS DIMEXP_date_date ON dbo.date_dim;
DROP INDEX IF EXISTS DIMEXP_date_month_seq ON dbo.date_dim;

IF @scenario = N'all_dimensions'
BEGIN
    CREATE NONCLUSTERED INDEX DIMEXP_customer_addr ON dbo.customer(c_current_addr_sk);
    CREATE NONCLUSTERED INDEX DIMEXP_address_state ON dbo.customer_address(ca_state);
    CREATE NONCLUSTERED INDEX DIMEXP_address_gmt ON dbo.customer_address(ca_gmt_offset);
    CREATE NONCLUSTERED INDEX DIMEXP_demo_education ON dbo.customer_demographics(cd_education_status);
    CREATE NONCLUSTERED INDEX DIMEXP_demo_marital ON dbo.customer_demographics(cd_marital_status);
    CREATE NONCLUSTERED INDEX DIMEXP_date_year ON dbo.date_dim(d_year);
    CREATE NONCLUSTERED INDEX DIMEXP_date_moy ON dbo.date_dim(d_moy);
    CREATE NONCLUSTERED INDEX DIMEXP_date_date ON dbo.date_dim(d_date);
    CREATE NONCLUSTERED INDEX DIMEXP_date_month_seq ON dbo.date_dim(d_month_seq);
END;
