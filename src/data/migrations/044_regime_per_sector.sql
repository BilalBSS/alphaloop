-- 044_regime_per_sector.sql
-- Phase 5 Step 4: widen regime_history.market to fit sector names.
-- Previously held only 'equity'/'crypto'; now also sector identifiers like 'mega_tech', 'cloud_cyber'.

ALTER TABLE regime_history ALTER COLUMN market TYPE VARCHAR(30);

-- mirror the widened column on regime_shifts so sector-level shift tracking works
ALTER TABLE regime_shifts ALTER COLUMN market TYPE VARCHAR(30);
