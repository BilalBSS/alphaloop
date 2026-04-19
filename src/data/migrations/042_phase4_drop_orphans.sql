-- / phase 4 cleanup: drop tables whose ingestion modules were deleted
-- / plus dead tables that had no writers/readers in any audit
-- / see docs/PHASE4_CHANGES.md step 3 for rationale

-- / modules deleted: earnings_transcripts, crypto_liquidations, performance_attribution
DROP TABLE IF EXISTS earnings_transcripts CASCADE;
DROP TABLE IF EXISTS crypto_liquidations CASCADE;
DROP TABLE IF EXISTS performance_attribution CASCADE;

-- / 13F holdings functions deleted from sec_filings.py (never called in prod)
DROP TABLE IF EXISTS institutional_holdings CASCADE;

-- / mig 005 orphans — created but never written/read
DROP TABLE IF EXISTS sector_profiles CASCADE;
DROP TABLE IF EXISTS symbol_profiles CASCADE;

-- / mig 037 orphan — created but nothing writes to it
DROP TABLE IF EXISTS ui_events CASCADE;

-- / retention config referenced these but no writer exists
DROP TABLE IF EXISTS notification_log CASCADE;
DROP TABLE IF EXISTS data_quality CASCADE;
