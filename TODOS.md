# TODOS

## Data Layer

### Fallback Fundamental Data Source
**Priority:** P2
**What:** Add a secondary data source for fundamentals (Financial Modeling Prep or Alpha Vantage) as a fallback when yfinance breaks.
**Why:** yfinance scrapes Yahoo Finance HTML — it has no SLA, breaks several times per year when Yahoo changes its interface. For live trading signals, bad fundamental data is a structural quality problem.
**Pros:** Resilience against yfinance outages, cross-validation of fundamental data.
**Cons:** FMP/Alpha Vantage free tiers have lower rate limits. May need paid tier eventually.
**Context:** Identified during Phase 2 eng review (outside voice). yfinance is fine for development but should have a fallback before the system trades live in Phase 7+.
**Depends on:** fundamentals.py (Phase 2, complete)

### Data Retention Policy for Neon 512MB
**Priority:** P3
**What:** Implement age-based data cleanup for high-volume tables (news_sentiment, data_quality, trade_log).
**Why:** Neon free tier is 512MB. Current data is small (~30MB/year) but news_sentiment (Phase 8) and DCF valuations (Phase 3, built) could exhaust storage before the full system is built.
**Pros:** Prevents silent write failures when storage is full.
**Cons:** Losing old data reduces backtesting depth. Need to balance retention vs storage.
**Context:** Identified during Phase 2 eng review (outside voice). Not urgent now but should be addressed before news_sentiment module is built in Phase 8.
**Depends on:** news_sentiment.py (Phase 8), dcf_model.py (Phase 3, complete)

## Analysis Engine

### Upgrade DCF Monte Carlo with Variance Reduction
**Priority:** P2
**What:** Add antithetic variates + stratified sampling to dcf_model.py's run_dcf_simulation().
**Why:** Current DCF uses crude MC (10k samples). Article reference shows stacking antithetic + stratified gives 100-500x variance reduction. Could achieve same precision with 100 samples or much tighter confidence intervals with 10k.
**Pros:** Tighter DCF fair value ranges, faster simulation, higher confidence scores.
**Cons:** Slightly more complex code. Marginal benefit if 10k crude samples are already precise enough for our use case.
**Context:** From quant article reference. Can be done as a quick upgrade to existing Phase 3 code, or deferred to Phase 6 when monte_carlo.py is built as a shared utility.
**Depends on:** dcf_model.py (Phase 3, complete)

## Completed
