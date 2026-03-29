-- / add edgar financial data columns to fundamentals table
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS shares_outstanding BIGINT;
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS net_debt DECIMAL(14,2);
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS total_revenue DECIMAL(14,2);
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS free_cash_flow DECIMAL(14,2);
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS total_debt DECIMAL(14,2);
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS total_cash DECIMAL(14,2);
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS data_source VARCHAR(20);
