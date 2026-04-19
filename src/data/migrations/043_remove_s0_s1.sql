-- Phase 4 QA cleanup: remove s0/s1 ghost strategies left over after config deletion
-- s0.json and s1.json were removed earlier in Phase 4 but rows in strategy_pool and
-- strategy_scores + wiki_documents persisted, making them show up as "killed" in the UI.
-- Archive their wiki pages rather than delete, so any inbound links stay resolvable.

-- strategy_pool is an in-memory Python class; persistence is via strategy_scores rows
DELETE FROM strategy_scores WHERE strategy_id IN ('s0', 's1');
DELETE FROM evolution_log WHERE strategy_id IN ('s0', 's1');
DELETE FROM trade_signals WHERE strategy_id IN ('s0', 's1');

UPDATE wiki_documents
SET category = 'archive'
WHERE path IN ('strategies/s0.md', 'strategies/s1.md');
