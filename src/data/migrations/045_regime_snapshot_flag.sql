-- 045_regime_snapshot_flag.sql
-- Phase 5 Step 3: daily regime snapshots so the timeline widget populates even
-- when no transition occurs. Flag distinguishes snapshot rows from shift rows
-- for potential future filtering (currently both are queried interchangeably).
-- wiki_documents already has word_count + confidence from migration 038; no
-- change needed there.

ALTER TABLE regime_history
    ADD COLUMN IF NOT EXISTS is_snapshot BOOLEAN NOT NULL DEFAULT FALSE;
