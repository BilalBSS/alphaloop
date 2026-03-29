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


## Analysis Engine

### Upgrade DCF Monte Carlo with Variance Reduction
**Priority:** P2
**What:** Add antithetic variates + stratified sampling to dcf_model.py's run_dcf_simulation().
**Why:** Current DCF uses crude MC (10k samples). Article reference shows stacking antithetic + stratified gives 100-500x variance reduction. Could achieve same precision with 100 samples or much tighter confidence intervals with 10k.
**Pros:** Tighter DCF fair value ranges, faster simulation, higher confidence scores.
**Cons:** Slightly more complex code. Marginal benefit if 10k crude samples are already precise enough for our use case.
**Context:** From quant article reference. Can be done as a quick upgrade to existing Phase 3 code, or deferred to Phase 6 when monte_carlo.py is built as a shared utility.
**Depends on:** dcf_model.py (Phase 3, complete)

## Broker Layer


### VWAP Session Reset for Intraday Data
**Priority:** P3
**What:** Add optional session boundary detection to vwap() so it resets the cumulative sum at each day boundary when fed intraday bars.
**Why:** VWAP is defined as a per-session indicator. The current implementation uses a running cumsum with no reset, which is correct for daily bars but produces meaningless values for multi-day intraday data.
**Pros:** Correct VWAP for intraday strategies.
**Cons:** Adds complexity. Not needed until intraday data is fed to the system.
**Context:** Identified during Phase 4 adversarial review. Current usage is daily bars only.
**Depends on:** volume.py (Phase 4, complete)

## Dashboard

### Symbol Deep-Dive Analysis Tab
**Priority:** P1
**What:** Expand the Analysis tab with a symbol picker that shows the full deep-dive for any symbol — fundamentals (P/E, P/S, DCF, earnings, insider), quant results (Monte Carlo, copula, particle filter), latest AI analyst summary, all indicator values, which strategies traded it, trade history for that symbol, and news sentiment.
**Why:** The dashboard currently shows portfolio-level data but no way to drill into individual symbols. The analyst scores 50 symbols but there's no UI to see what it found.
**Pros:** Full visibility into why the system is (or isn't) trading a symbol. Makes the system debuggable and trustworthy.
**Cons:** Requires expanding the `/api/analysis/{symbol}` endpoint to return all data from multiple tables, plus a new React component.
**Context:** The API endpoint exists but only returns analysis_scores + recent signals. Needs to join against fundamentals, dcf_valuations, news_sentiment, trade_log, and indicator computations.
**Depends on:** dashboard app.py (complete), React frontend (complete)

## Crypto

### Alternative Exchange for Non-Alpaca Tokens
**Priority:** P2
**What:** Add Coinbase or Bybit broker integration for crypto tokens not listed on Alpaca (HYPE, PUMP, ASTER, and future meme/small-cap tokens).
**Why:** Alpaca only lists major crypto. User wants to trade newer tokens that aren't available on Alpaca.
**Pros:** Full coverage of user's preferred crypto universe.
**Cons:** New broker implementation, different API, separate account needed. Significant effort.
**Context:** User's preferred crypto includes HYPE (Hyperliquid), PUMP (PumpFun), ASTER — none on Alpaca. The broker layer is modular (BrokerInterface), so adding a new exchange is architecturally clean but requires implementation + testing.
**Depends on:** broker_factory.py (complete), base.py BrokerInterface (complete)

### Finnhub Sentiment Endpoint Fix
**Priority:** P3
**What:** Switch from Finnhub `/news-sentiment` (paid only) to `/company-news` endpoint (free tier) for sentiment scoring. The keyword fallback already works but could be improved.
**Why:** Finnhub `/news-sentiment` returns 403 on free tier for all symbols. The system falls back to keyword scoring on `/company-news` headlines, but this path has been failing silently due to the retry loop burning time.
**Pros:** Faster analyst cycles (skip 3 retries on 403), better sentiment data.
**Cons:** Keyword scoring is less accurate than Finnhub's built-in NLP.
**Context:** Visible in VPS logs — every symbol gets 2 retries on 403 before falling back. Wastes ~5 seconds per symbol * 43 stock symbols = ~3.5 minutes per analyst cycle.
**Depends on:** news_sentiment.py (complete)

## Completed

### Data Retention Policy for Neon 512MB
**Completed:** v0.8.0 (2026-03-27)
Implemented cleanup_old_data() in db.py with configurable retention: news_sentiment 180d, crypto_onchain 180d, data_quality 180d, notification_log 30d. Called at start of backfill and end of evolution cycle.

### Reusable httpx.AsyncClient
**Completed:** v0.8.0 (2026-03-27)
Added shared HTTP client (api_get/api_post) in resilience.py with per-source rate limiting. All data source modules use it instead of managing their own clients.

### AsyncIO Lock for PaperBroker
**Completed:** v0.7.0.0 (2026-03-26)
Added asyncio.Lock around PaperBroker.place_order to prevent concurrent double-spending. Required by Phase 7 orchestrator running concurrent agent loops.
