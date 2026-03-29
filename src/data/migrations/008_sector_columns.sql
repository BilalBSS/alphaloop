-- / add sector averages for fcf margin, debt/equity, revenue growth
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS sector_fcf_margin_avg DECIMAL(8,4);
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS sector_de_avg DECIMAL(10,2);
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS sector_rev_growth_avg DECIMAL(8,4);
