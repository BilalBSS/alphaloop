-- / phase 6 step 12: partial-exit tracking per position
-- / set true once the first take_profit tier has fired so we don't re-fire each cycle

ALTER TABLE strategy_positions
    ADD COLUMN IF NOT EXISTS partial_exit_fired BOOLEAN NOT NULL DEFAULT FALSE;
