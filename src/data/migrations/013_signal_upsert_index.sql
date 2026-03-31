-- 013_signal_upsert_index.sql
-- signal dedup handled in application layer (check-then-insert in tools.py)
-- timestamptz::date is not immutable so cannot be used in a unique index
SELECT 1;
