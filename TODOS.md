# TODOS

## Data Layer

### Fallback Fundamental Data Source
**Priority:** P2
**What:** Add a secondary data source for fundamentals (Financial Modeling Prep or Alpha Vantage) as a fallback when yfinance breaks.
**Why:** yfinance scrapes Yahoo Finance HTML — it has no SLA, breaks several times per year when Yahoo changes its interface. For live trading signals, bad fundamental data is a structural quality problem.
**Pros:** Resilience against yfinance outages, cross-validation of fundamental data.
**Cons:** FMP/Alpha Vantage free tiers have lower rate limits. May need paid tier eventually.
**Context:** Identified during Phase 2 eng review (outside voice). yfinance is fine for Phase 2-3 development but should have a fallback before the system trades live in Phase 6+.
**Depends on:** fundamentals.py (Phase 2, complete)

### Data Retention Policy for Neon 512MB
**Priority:** P3
**What:** Implement age-based data cleanup for high-volume tables (news_sentiment, data_quality, trade_log).
**Why:** Neon free tier is 512MB. Phase 2 data is small (~30MB/year) but news_sentiment (Phase 7) and DCF valuations (Phase 3) could exhaust storage before the full system is built.
**Pros:** Prevents silent write failures when storage is full.
**Cons:** Losing old data reduces backtesting depth. Need to balance retention vs storage.
**Context:** Identified during Phase 2 eng review (outside voice). Not urgent for Phase 2 but should be addressed before news_sentiment or DCF modules are built.
**Depends on:** news_sentiment.py (Phase 7), dcf_model.py (Phase 3)

## Completed
