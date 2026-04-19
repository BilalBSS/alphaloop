#!/usr/bin/env python3
# / one-shot batch backtest for all configs/strategies/*.json
# / populates metadata fields (backtest_sharpe, backtest_max_drawdown,
# / backtest_win_rate, sortino, trade_count) that the evolution promotion gate
# / needs. brier_score + paper_trade_days stay null — those are earned in paper
# / trading.
# /
# / usage:
# /   python -m scripts.backtest_all_strategies            # 2y history, all strategies
# /   python -m scripts.backtest_all_strategies --years 1  # shorter window
# /   python -m scripts.backtest_all_strategies --ids strategy_007,strategy_012
# /
# / failure policy: per-strategy try/except; any failure → status=inactive in
# / its config JSON and we continue. hard time cap at --timeout-seconds (default 10800=3h).

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import traceback
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import structlog

# / add project root to path so `python -m scripts.backtest_all_strategies` works
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv  # noqa: E402
load_dotenv()

from src.data.db import init_db, close_db  # noqa: E402
from src.data.symbols import FULL_UNIVERSE, resolve_universe, is_crypto  # noqa: E402
from src.strategies.strategy_loader import load_config_file, CONFIGS_DIR  # noqa: E402
from src.strategies.backtest import run_backtest  # noqa: E402

logger = structlog.get_logger(__name__)


# / ---- market data ----

async def _fetch_available_symbols(pool) -> list[str]:
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT DISTINCT symbol FROM market_data")
    return sorted({r["symbol"] for r in rows})


async def _load_ohlcv(pool, symbol: str, years: int) -> pd.DataFrame | None:
    start = date.today() - timedelta(days=365 * years)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT date, open, high, low, close, volume
            FROM market_data
            WHERE symbol = $1 AND date >= $2
            ORDER BY date ASC""",
            symbol, start,
        )
    if not rows or len(rows) < 60:
        return None
    df = pd.DataFrame(
        [{
            "open": float(r["open"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "close": float(r["close"]),
            "volume": float(r["volume"]) if r["volume"] is not None else 0.0,
        } for r in rows],
        index=[pd.Timestamp(r["date"]) for r in rows],
    )
    df.index.name = "date"
    return df


# / ---- metadata writer ----

def _write_metadata_atomic(path: Path, metadata_patch: dict[str, Any]) -> None:
    # / read-modify-write with atomic rename so a mid-write kill doesn't corrupt json
    with open(path, encoding="utf-8") as f:
        config = json.load(f)

    metadata = config.get("metadata") or {}
    metadata.update(metadata_patch)
    config["metadata"] = metadata

    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
        f.write("\n")
    tmp.replace(path)


def _seed_inactive(path: Path, reason: str) -> None:
    _write_metadata_atomic(path, {
        "status": "inactive",
        "backtest_error": reason[:500],
        "backtest_ran_at": pd.Timestamp.utcnow().isoformat(),
    })


# / ---- per-strategy runner ----

async def _run_one(
    pool,
    config_path: Path,
    years: int,
    per_strategy_timeout_s: float,
    symbol_cache: dict[str, pd.DataFrame],
    available_symbols: list[str],
) -> dict[str, Any]:
    strategy_id = config_path.stem
    t0 = time.monotonic()
    try:
        strategy = load_config_file(config_path)
    except Exception as exc:
        _seed_inactive(config_path, f"config_load_failed: {exc}")
        logger.error("strategy_load_failed", strategy=strategy_id, error=str(exc)[:200])
        return {"id": strategy_id, "status": "inactive", "reason": "config_load_failed"}

    universe_ref = strategy.config.get("universe", "all_stocks")
    try:
        symbols = resolve_universe(str(universe_ref), available_symbols=available_symbols)
    except Exception as exc:
        _seed_inactive(config_path, f"universe_resolve_failed: {exc}")
        return {"id": strategy_id, "status": "inactive", "reason": "universe_resolve_failed"}

    # / strategy.config["tier"] can scope to a single symbol or sector — honor it
    tier = strategy.config.get("tier")
    if tier == "symbol" and strategy.config.get("symbol"):
        symbols = [strategy.config["symbol"]]
    elif tier == "sector" and strategy.config.get("sector"):
        from src.data.symbols import SECTORS
        sec = strategy.config["sector"]
        if sec in SECTORS:
            symbols = SECTORS[sec]

    # / filter to symbols we actually have data for
    symbols = [s for s in symbols if s in set(available_symbols)]
    if not symbols:
        _seed_inactive(config_path, "no_market_data_for_universe")
        return {"id": strategy_id, "status": "inactive", "reason": "no_market_data"}

    # / load ohlcv (cached across strategies to avoid re-querying)
    market_data: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        if sym in symbol_cache:
            market_data[sym] = symbol_cache[sym]
            continue
        df = await _load_ohlcv(pool, sym, years)
        if df is None or df.empty:
            continue
        symbol_cache[sym] = df
        market_data[sym] = df

    if not market_data:
        _seed_inactive(config_path, "no_ohlcv_in_window")
        return {"id": strategy_id, "status": "inactive", "reason": "no_ohlcv"}

    logger.info(
        "backtest_start", strategy=strategy_id, universe_size=len(market_data),
        years=years,
    )

    try:
        result = await asyncio.wait_for(
            run_backtest(strategy, market_data, initial_cash=100_000.0),
            timeout=per_strategy_timeout_s,
        )
    except asyncio.TimeoutError:
        _seed_inactive(config_path, f"backtest_timeout_{int(per_strategy_timeout_s)}s")
        return {"id": strategy_id, "status": "inactive", "reason": "timeout"}
    except Exception as exc:
        tb = traceback.format_exc()
        logger.error("backtest_failed", strategy=strategy_id, error=str(exc)[:400])
        _seed_inactive(config_path, f"backtest_exception: {exc.__class__.__name__}: {exc}\n{tb[-800:]}")
        return {"id": strategy_id, "status": "inactive", "reason": "exception"}

    # / validate metrics aren't NaN/inf before persisting
    import math
    metrics_ok = all(
        isinstance(v, (int, float)) and math.isfinite(v)
        for v in (result.sharpe_ratio, result.max_drawdown_pct, result.win_rate,
                  result.total_return_pct, result.sortino_ratio)
    )
    if not metrics_ok or result.total_trades == 0:
        _write_metadata_atomic(config_path, {
            "status": "paper_trading",  # / don't kill — just mark unvalidated
            "backtest_sharpe": None,
            "backtest_max_drawdown": None,
            "backtest_win_rate": None,
            "backtest_trade_count": int(result.total_trades),
            "backtest_ran_at": pd.Timestamp.utcnow().isoformat(),
            "backtest_error": "no_trades_or_nonfinite",
        })
        return {"id": strategy_id, "status": "paper_trading", "reason": "no_trades"}

    patch = {
        "status": strategy.config.get("metadata", {}).get("status") or "paper_trading",
        "backtest_sharpe": round(float(result.sharpe_ratio), 4),
        "backtest_sortino": round(float(result.sortino_ratio), 4) if math.isfinite(result.sortino_ratio) else None,
        "backtest_calmar": round(float(result.calmar_ratio), 4) if math.isfinite(result.calmar_ratio) else None,
        "backtest_max_drawdown": round(float(result.max_drawdown_pct), 4),
        "backtest_win_rate": round(float(result.win_rate), 4),
        "backtest_total_return_pct": round(float(result.total_return_pct), 4),
        "backtest_trade_count": int(result.total_trades),
        "backtest_avg_holding_days": round(float(result.avg_holding_days), 2),
        "backtest_profit_factor": round(float(result.profit_factor), 4) if math.isfinite(result.profit_factor) else None,
        "backtest_ran_at": pd.Timestamp.utcnow().isoformat(),
        "backtest_window_years": years,
        "backtest_error": None,
    }
    _write_metadata_atomic(config_path, patch)

    elapsed = time.monotonic() - t0
    logger.info(
        "backtest_done", strategy=strategy_id,
        trades=result.total_trades, sharpe=round(result.sharpe_ratio, 3),
        mdd=round(result.max_drawdown_pct, 3), win_rate=round(result.win_rate, 3),
        elapsed_s=round(elapsed, 1),
    )
    return {
        "id": strategy_id,
        "status": patch["status"],
        "sharpe": patch["backtest_sharpe"],
        "mdd": patch["backtest_max_drawdown"],
        "win_rate": patch["backtest_win_rate"],
        "trades": patch["backtest_trade_count"],
        "elapsed_s": round(elapsed, 1),
    }


# / ---- main ----

async def main() -> int:
    parser = argparse.ArgumentParser(description="Batch-backtest every strategy config.")
    parser.add_argument("--years", type=int, default=2, help="Years of history (default 2)")
    parser.add_argument("--ids", type=str, default=None,
                        help="Comma-separated strategy IDs to run (default: all)")
    parser.add_argument("--timeout-seconds", type=int, default=10800,
                        help="Overall hard cap in seconds (default 10800=3h)")
    parser.add_argument("--per-strategy-timeout", type=int, default=600,
                        help="Per-strategy timeout in seconds (default 600=10m)")
    parser.add_argument("--directory", type=str, default=None,
                        help="Strategy configs directory (default configs/strategies)")
    args = parser.parse_args()

    directory = Path(args.directory) if args.directory else CONFIGS_DIR
    if not directory.exists():
        logger.error("config_dir_missing", path=str(directory))
        return 1

    # / legacy s0.json/s1.json are placeholder test artifacts — skip them
    all_configs = [
        p for p in sorted(directory.glob("strategy_*.json"))
        if p.stem.startswith("strategy_")
    ]
    if args.ids:
        wanted = {s.strip() for s in args.ids.split(",") if s.strip()}
        all_configs = [p for p in all_configs if p.stem in wanted]

    if not all_configs:
        logger.error("no_configs_found", directory=str(directory))
        return 1

    logger.info("batch_backtest_start", count=len(all_configs), years=args.years,
                timeout_s=args.timeout_seconds)

    pool = await init_db()
    try:
        try:
            available = await _fetch_available_symbols(pool)
        except Exception as exc:
            logger.error("symbol_discovery_failed", error=str(exc)[:200])
            return 2
        if not available:
            logger.error("no_market_data_in_db",
                         hint="run `python -m scripts.backfill --years 2` first")
            return 2

        logger.info("symbols_available", count=len(available))
        symbol_cache: dict[str, pd.DataFrame] = {}
        results: list[dict[str, Any]] = []

        overall_deadline = time.monotonic() + float(args.timeout_seconds)

        for idx, config_path in enumerate(all_configs):
            remaining = overall_deadline - time.monotonic()
            if remaining <= 0:
                logger.warning(
                    "overall_timeout_reached",
                    completed=idx, remaining_skipped=len(all_configs) - idx,
                )
                # / mark remaining inactive so promotion gate has a paper trail
                for skipped in all_configs[idx:]:
                    try:
                        _seed_inactive(skipped, "overall_timeout_before_backtest")
                    except Exception:
                        pass
                break

            per_s = min(float(args.per_strategy_timeout), remaining)
            out = await _run_one(
                pool, config_path, args.years, per_s,
                symbol_cache, available,
            )
            results.append(out)

        # / summary
        ran = [r for r in results if r.get("status") != "inactive"]
        killed = [r for r in results if r.get("status") == "inactive"]
        logger.info(
            "batch_backtest_complete",
            total=len(results), ran=len(ran), inactive=len(killed),
            top_sharpe=sorted(
                [r for r in ran if r.get("sharpe") is not None],
                key=lambda r: r.get("sharpe") or -9e9, reverse=True,
            )[:5],
        )
    finally:
        await close_db()

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
