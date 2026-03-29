-- / add net income column to fundamentals table
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS net_income DECIMAL(14,2);
