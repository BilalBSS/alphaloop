-- bug e retroactive cleanup: scrub NaN decimal values inserted before _to_decimal_safe fix
-- idempotent: only updates rows where the numeric value is literally NaN
UPDATE insider_trades SET shares = NULL WHERE shares = 'NaN'::DECIMAL;
UPDATE insider_trades SET price_per_share = NULL WHERE price_per_share = 'NaN'::DECIMAL;
UPDATE insider_trades SET total_value = NULL WHERE total_value = 'NaN'::DECIMAL;
