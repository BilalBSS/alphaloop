# Quant Trading System

Algorithmic trading system that trades US equities and crypto through Alpaca's API. The whole thing runs on a single VPS for under $25/month — no Bloomberg terminal, no expensive data feeds, just free-tier APIs stitched together properly.

## What it does

The system scores stocks using fundamental analysis (DCF models, earnings surprises, insider activity, financial ratios), times entries with technical indicators, and manages risk through a multi-agent architecture. Strategies are defined as JSON configs, not code — so an LLM can propose mutations, backtest them, kill the losers, and promote the winners. Think genetic algorithms but for trading strategies, inspired by Karpathy's autoresearch loop.

Every strategy has two layers: a fundamental base (always on — P/E, DCF upside, insider buying, earnings beats) and a technical signal layer (varies — bollinger oversold + RSI divergence, MACD crossover + volume confirmation, etc). Fundamentals decide *what* to trade. Technicals decide *when*.

## Architecture

Python 3.11, async everywhere. PostgreSQL on Neon free tier for persistence. Structured logging with structlog.

**Data layer** — Pulls market data from Alpaca, fundamentals from yfinance, insider trades from SEC EDGAR (edgartools), market regime from volatility/breadth indicators. Built-in retry + circuit breaker for all external calls. Data validation layer catches bad data before it hits the DB.

**Analysis engine** — Financial ratio scoring (P/E, P/S, PEG, D/E, FCF margin → 0-100 composite), Monte Carlo DCF (10k simulations → p10/median/p90 fair value distribution), sensitivity grids (growth rate × terminal multiple), earnings surprise detection with beat/miss streaks, insider activity aggregation with title-weighted scoring (CEO buys matter more than director buys), and LLM-powered summaries via Groq free tier with structured fallback.

**Technical indicators** — 17 indicators across four categories, all built on pandas/numpy with Wilder smoothing where appropriate:
- Trend: SMA, EMA, MACD, ADX, Supertrend
- Momentum: RSI, Stochastic, CCI, Williams %R, ROC
- Volatility: Bollinger Bands, ATR, Keltner Channel
- Volume: OBV, VWAP, Volume Profile, MFI

**Broker layer** — Abstract interface with two implementations: PaperBroker (in-memory simulation for backtesting — tracks positions, handles limit orders, rejects insufficient funds) and AlpacaBroker (REST API for live/paper trading, supports stocks and crypto). BrokerFactory routes by mode. The abstraction means swapping brokers or adding Coinbase later is just a new class.

**Strategy framework** — Strategies are JSON configs, not code. Each config defines entry signals (8 indicator types), exit conditions (stop loss, take profit, time exit), fundamental filters (7 filter types), and position sizing. A Pydantic loader validates configs against dual-track constraints: fundamental-gated strategies (real fundamental filters set) get up to 8% position sizing with ≥2 technical signals; momentum-only strategies get 4% max with ≥1 signal. The strategy pool manages N concurrent strategies with composite scoring, ranked views, and bottom-quartile detection for the evolution engine to kill.

**Backtesting engine** — Anti-lookahead by construction: entry signals evaluated at previous bar close, filled at next bar open. Uses PaperBroker for realistic fills. Computes Sharpe, Sortino, Calmar (compound annualized), max drawdown, win rate, profit factor, avg holding days. 10 seed strategies ship as starting material for the evolution engine — 4 fundamental-gated (value/mean-reversion) and 6 momentum-only (trend/breakout).

**Planned (designed, not yet built):**
- Quant engine: Monte Carlo with variance reduction (antithetic + control variates + stratified sampling), importance sampling for tail risk, particle filter for real-time signal smoothing, Student-t copulas for portfolio tail dependence
- Multi-agent system: analyst, strategy, risk, and executor agents coordinated by an orchestrator
- Evolution engine: nightly loop that ranks strategies, kills bottom 25%, mutates winners via LLM, backtests mutations, promotes survivors
- ICT/smart money indicators (Fair Value Gaps, Order Blocks, BOS/CHoCH)
- On-chain crypto analytics via Dune/DefiLlama
- FastAPI dashboard

## Technical decisions worth noting

**DCF uses Monte Carlo, not a single point estimate.** A single DCF number is meaningless — the output is a distribution. 10k simulations with randomized growth rates, margins, and terminal multiples give you p10/p90 confidence intervals. The sensitivity grid shows which assumption matters most for each stock.

**Wilder smoothing for RSI/ATR/ADX, not standard EMA.** Wilder's original specification uses alpha=1/period, which gives a slower, smoother response than the typical EMA span. Most charting platforms get this wrong.

**Broker retry is read-only.** The Alpaca broker retries GET requests (prices, positions, account) but never retries POST (place_order). Retrying a write after a timeout can create duplicate orders with real money.

**Paper broker fills limit orders at the better price.** If you place a limit buy at $160 and the market is at $150, you get filled at $150 — not $160. Most paper trading implementations get this wrong, which makes backtests unrealistically pessimistic.

**Regime detection gates everything.** Market regime (bull/bear/sideways/high-vol) is classified from volatility and breadth indicators. Strategies can require specific regimes — a mean-reversion strategy shouldn't fire during a trending market.

## Numbers

- 648 tests across 26 test files, all passing
- 5 phases complete (foundation → data layer → analysis engine → indicators + broker → strategy + backtesting)
- 4 phases remaining (quant engine → agents + evolution → advanced indicators → dashboard)
- 35+ source modules, 10 seed strategy configs
- Zero external paid services (everything runs on free tiers)

## Stack

Python 3.11 · asyncpg · httpx · pandas · numpy · scipy · structlog · pytest · Neon PostgreSQL · Alpaca API · yfinance · edgartools · Groq
