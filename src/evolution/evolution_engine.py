# / karpathy autoresearch loop for strategy evolution
# / read -> rank -> kill -> mutate -> backtest -> score -> promote -> document

from __future__ import annotations

import asyncio
from typing import Any

import numpy as np
import structlog

from src.agents.tools import (
    count_all_symbol_trades,
    count_symbol_trades,
    fetch_recent_trades,
    fetch_strategy_scores,
    store_evolution_log,
    store_strategy_score,
)
from src.evolution.documentation import update_docs
from src.evolution.report_generator import REPORTS_DIR, generate_report
from src.evolution.strategy_mutator import mutate_strategy
from src.strategies.backtest import BacktestResult, run_backtest
from src.strategies.base_strategy import ConfigDrivenStrategy
from src.strategies.strategy_loader import save_config
from src.notifications.notifier import notify_evolution_summary, notify_strategy_promoted
from src.data.symbols import get_sector_symbols
from src.strategies.strategy_pool import (
    StrategyPool,
    StrategyScore,
    compute_composite_score,
)

logger = structlog.get_logger(__name__)


class EvolutionEngine:
    def __init__(self, rng: np.random.Generator | None = None, risk_limits: dict | None = None):
        self._rng = rng or np.random.default_rng()
        evo = (risk_limits or {}).get("evolution", {})
        self._tier2_spawn_trades = evo.get("tier2_spawn_trades", 20)
        self._tier2_kill_trades = evo.get("tier2_kill_trades", 20)
        self._tier3_graduate_trades = evo.get("tier3_graduate_trades", 100)
        self._tier3_sharpe_delta = evo.get("tier3_sharpe_delta", 0.2)

    async def run(
        self,
        pool: Any,
        strategy_pool: StrategyPool,
        market_data: dict | None = None,
    ) -> dict[str, Any]:
        # / main evolution loop
        # / pool = asyncpg connection pool for db operations
        # / strategy_pool = in-memory strategy pool
        # / market_data = historical data for backtesting mutations

        summary: dict[str, Any] = {
            "generation": 0,
            "killed": [],
            "mutated": [],
            "promoted": [],
            "errors": [],
        }

        # / bail early if pool is empty
        if strategy_pool.size == 0:
            logger.info("evolution_empty_pool")
            return summary

        # / 1. READ: fetch strategy scores from db
        db_scores, generation = await self._read_scores(pool)
        summary["generation"] = generation
        logger.info("evolution_start", generation=generation, pool_size=strategy_pool.size)

        # / 2. UPDATE pool scores from db
        self._update_pool(db_scores, strategy_pool)

        # / 3. KILL: bottom quartile
        killed_configs = await self._kill_bottom_quartile(pool, generation, strategy_pool, summary)

        # / 4. KILL: tier-2 underperformers
        await self._kill_underperforming_tier2(pool, generation, strategy_pool, killed_configs, summary)
        logger.info("evolution_killed", count=len(killed_configs))

        # / 5-6. MUTATE + BACKTEST
        mutated_configs = await self._mutate_killed(pool, killed_configs, strategy_pool, summary)
        backtest_results = await self._backtest_mutated(mutated_configs, market_data, summary)

        # / 7-8. SCORE + ADD above-median to pool
        await self._score_and_add(pool, generation, backtest_results, strategy_pool, summary)

        # / 9. PROMOTE: paper_trading -> live
        await self._promote_paper(pool, generation, strategy_pool, summary)

        # / 10. SPAWN TIER-2: per-symbol tweaks from sector strategies
        try:
            spawned = await self._spawn_tier2(pool, strategy_pool, generation)
            summary["spawned_tier2"] = spawned
        except Exception as exc:
            logger.error("spawn_tier2_failed", error=str(exc))
            summary["errors"].append(f"spawn_tier2 failed: {exc}")

        # / 11. GRADUATE TIER-3: per-symbol full freedom
        try:
            graduated = await self._graduate_tier3(pool, strategy_pool, generation)
            summary["graduated_tier3"] = graduated
        except Exception as exc:
            logger.error("graduate_tier3_failed", error=str(exc))
            summary["errors"].append(f"graduate_tier3 failed: {exc}")

        # / 12. DOCUMENT: generate report and update docs
        await self._document(pool, generation, strategy_pool, summary)

        notify_evolution_summary(summary)
        logger.info(
            "evolution_complete",
            generation=generation,
            killed=len(summary["killed"]),
            mutated=len(summary["mutated"]),
            promoted=len(summary["promoted"]),
        )
        return summary

    async def _read_scores(self, pool: Any) -> tuple[list, int]:
        # / 1. READ: fetch strategy scores from db
        try:
            db_scores = await fetch_strategy_scores(pool)
        except Exception as exc:
            logger.error("fetch_scores_failed", error=str(exc))
            db_scores = []

        # / determine generation counter
        generation = 1
        if db_scores:
            # / evolution_log generation tracking via db scores
            try:
                rows = await pool.fetch(
                    "SELECT COALESCE(MAX(generation), 0) as max_gen FROM evolution_log"
                )
                if rows:
                    generation = int(rows[0]["max_gen"]) + 1
            except Exception:
                generation = 1

        return db_scores, generation

    def _update_pool(self, db_scores: list, strategy_pool: StrategyPool) -> None:
        # / update pool scores from db
        for score_row in db_scores:
            sid = score_row.get("strategy_id", "")
            entry = strategy_pool.get(sid)
            if entry is not None:
                s = StrategyScore(
                    strategy_id=sid,
                    sharpe_ratio=float(score_row.get("sharpe_ratio", 0)),
                    max_drawdown=float(score_row.get("max_drawdown", 0)),
                    win_rate=float(score_row.get("win_rate", 0)),
                    total_trades=int(score_row.get("total_trades", 0)),
                    brier_score=float(score_row["brier_score"]) if score_row.get("brier_score") is not None else None,
                )
                strategy_pool.update_score(sid, s)

    async def _kill_bottom_quartile(
        self, pool: Any, generation: int, strategy_pool: StrategyPool, summary: dict,
    ) -> list[dict]:
        # / 2. RANK + 3. KILL: bottom quartile
        # / skip killing if strategies haven't accumulated enough trade data
        scored_count = sum(
            1 for e in strategy_pool.ranked()
            if e.score and e.score.total_trades >= 5
        )
        if scored_count < 4:
            logger.info("evolution_skip_kill", reason="not enough strategies with trades", scored=scored_count)
            bottom = []
        else:
            bottom = strategy_pool.bottom_quartile()
        killed_configs: list[dict] = []
        for entry in bottom:
            sid = entry.strategy.strategy_id
            config = entry.strategy.config
            reason = "bottom quartile"
            if entry.score:
                reason += f" (composite={entry.score.composite_score:.4f})"

            strategy_pool.update_status(sid, "killed")
            killed_configs.append({"id": sid, "config": config, "reason": reason})
            summary["killed"].append({"id": sid, "reason": reason})

            try:
                await store_evolution_log(
                    pool, generation, "kill", sid,
                    config.get("parent_id"), reason,
                )
            except Exception as exc:
                logger.error("evolution_log_kill_failed", strategy_id=sid, error=str(exc))

        return killed_configs

    async def _kill_underperforming_tier2(
        self, pool: Any, generation: int, strategy_pool: StrategyPool,
        killed_configs: list[dict], summary: dict,
    ) -> None:
        # / tier-2 kill condition: tweaked strategies that don't beat sector base
        for entry in strategy_pool.list_by_status("live"):
            config = entry.strategy.config
            if config.get("tier") != "tweaked":
                continue
            if not entry.score or entry.score.total_trades < self._tier2_kill_trades:
                continue
            sector_sharpe = self._get_sector_base_sharpe(strategy_pool, config.get("sector"))
            if entry.score.sharpe_ratio < sector_sharpe:
                sid = entry.strategy.strategy_id
                reason = f"tier2 underperforms sector base (sharpe {entry.score.sharpe_ratio:.2f} < {sector_sharpe:.2f})"
                strategy_pool.update_status(sid, "killed")
                killed_configs.append({"id": sid, "config": config, "reason": reason})
                summary["killed"].append({"id": sid, "reason": reason})
                try:
                    await store_evolution_log(pool, generation, "kill", sid, config.get("parent_id"), reason)
                except Exception as exc:
                    logger.error("evolution_log_tier2_kill_failed", error=str(exc))

    async def _mutate_killed(
        self, pool: Any, killed_configs: list[dict], strategy_pool: StrategyPool,
        summary: dict,
    ) -> list[dict]:
        # / 4. MUTATE: propose mutations for each killed strategy
        top_performers = strategy_pool.top_performers(n=1)
        top_config = top_performers[0].strategy.config if top_performers else {}

        mutation_tasks = []
        for killed in killed_configs:
            sid = killed["id"]
            try:
                trades = await fetch_recent_trades(pool, strategy_id=sid, limit=10)
            except Exception:
                trades = []

            mutation_tasks.append(
                mutate_strategy(killed["config"], top_config, trades, rng=self._rng)
            )

        mutated_configs: list[dict] = []
        if mutation_tasks:
            results = await asyncio.gather(*mutation_tasks, return_exceptions=True)
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error("mutation_failed", error=str(result))
                    summary["errors"].append(f"mutation failed: {result}")
                elif isinstance(result, list):
                    # / dual-model: mutator returns list of configs (1 or 2)
                    mutated_configs.extend(result)
                else:
                    mutated_configs.append(result)

        return mutated_configs

    async def _backtest_mutated(
        self, mutated_configs: list[dict], market_data: dict | None,
        summary: dict,
    ) -> list[tuple[dict, BacktestResult]]:
        # / 5. BACKTEST: run backtests in parallel
        backtest_results: list[tuple[dict, BacktestResult]] = []
        if mutated_configs and market_data:
            backtest_tasks = []
            for config in mutated_configs:
                strategy = ConfigDrivenStrategy(config)
                backtest_tasks.append(run_backtest(strategy, market_data))

            bt_results = await asyncio.gather(*backtest_tasks, return_exceptions=True)
            for config, bt_result in zip(mutated_configs, bt_results):
                if isinstance(bt_result, Exception):
                    logger.error("backtest_failed", strategy_id=config.get("id"), error=str(bt_result))
                    summary["errors"].append(f"backtest failed for {config.get('id')}: {bt_result}")
                else:
                    backtest_results.append((config, bt_result))

        return backtest_results

    async def _score_and_add(
        self, pool: Any, generation: int,
        backtest_results: list[tuple[dict, BacktestResult]],
        strategy_pool: StrategyPool, summary: dict,
    ) -> None:
        # / 6. SCORE + 7. ADD above-median to pool
        # / compute median composite score of current pool
        ranked = strategy_pool.ranked()
        if ranked:
            composites = [
                e.score.composite_score for e in ranked
                if e.score is not None
            ]
            median_score = float(np.median(composites)) if composites else 0.0
        else:
            median_score = 0.0

        for config, bt_result in backtest_results:
            composite = compute_composite_score(
                sharpe=bt_result.sharpe_ratio,
                win_rate=bt_result.win_rate,
                max_drawdown=bt_result.max_drawdown_pct,
            )

            mutation_entry = {
                "id": config.get("id", "unknown"),
                "parent_id": config.get("parent_id", "unknown"),
                "composite": composite,
            }

            if composite > median_score:
                # / add to pool as paper_trading
                config["metadata"]["status"] = "paper_trading"
                strategy = ConfigDrivenStrategy(config)
                strategy_pool.add(strategy, status="paper_trading")

                score = StrategyScore(
                    strategy_id=config["id"],
                    sharpe_ratio=bt_result.sharpe_ratio,
                    max_drawdown=bt_result.max_drawdown_pct,
                    win_rate=bt_result.win_rate,
                    total_trades=bt_result.total_trades,
                    total_pnl=bt_result.total_return,
                )
                strategy_pool.update_score(config["id"], score)

                # / persist score to db for dashboard quant metrics
                try:
                    from datetime import date as dt_date
                    p_start = bt_result.period_start.date() if bt_result.period_start else dt_date.today()
                    p_end = bt_result.period_end.date() if bt_result.period_end else dt_date.today()
                    import math
                    sortino = bt_result.sortino_ratio if math.isfinite(bt_result.sortino_ratio) else (99.0 if bt_result.sortino_ratio > 0 else -99.0)
                    await store_strategy_score(
                        pool, config["id"], p_start, p_end,
                        sharpe_ratio=bt_result.sharpe_ratio,
                        max_drawdown=bt_result.max_drawdown_pct,
                        win_rate=bt_result.win_rate,
                        brier_score=None,
                        total_trades=bt_result.total_trades,
                        sortino_ratio=sortino,
                        composite_score=composite,
                    )
                except Exception as exc:
                    logger.error("store_strategy_score_failed", strategy_id=config["id"], error=str(exc))

                mutation_entry["status"] = "paper_trading"
                logger.info("mutation_added_to_pool", strategy_id=config["id"], composite=composite)

                try:
                    save_config(config)
                except Exception as exc:
                    logger.error("save_config_failed", error=str(exc))

                try:
                    await store_evolution_log(
                        pool, generation, "mutate", config["id"],
                        config.get("parent_id"), f"above median ({composite:.4f} > {median_score:.4f})",
                    )
                except Exception as exc:
                    logger.error("evolution_log_mutate_failed", error=str(exc))
            else:
                mutation_entry["status"] = "discarded"
                logger.info("mutation_discarded", strategy_id=config.get("id"), composite=composite, median=median_score)

            summary["mutated"].append(mutation_entry)

    async def _promote_paper(
        self, pool: Any, generation: int, strategy_pool: StrategyPool, summary: dict,
    ) -> None:
        # / 8. PROMOTE: paper_trading strategies with 14+ days and sharpe >= 0.8
        paper_strategies = strategy_pool.list_by_status("paper_trading")
        for entry in paper_strategies:
            paper_days = entry.strategy.config.get("metadata", {}).get("paper_trade_days", 0)
            if entry.score and paper_days >= 14 and entry.score.sharpe_ratio >= 0.8:
                sid = entry.strategy.strategy_id
                strategy_pool.update_status(sid, "live")
                summary["promoted"].append({"id": sid})
                notify_strategy_promoted(sid, entry.score.sharpe_ratio, paper_days)
                logger.info("strategy_promoted", strategy_id=sid, sharpe=entry.score.sharpe_ratio, days=paper_days)

                try:
                    await store_evolution_log(
                        pool, generation, "promote", sid,
                        entry.strategy.config.get("parent_id"),
                        f"paper_trading {paper_days}d, sharpe={entry.score.sharpe_ratio:.2f}",
                    )
                except Exception as exc:
                    logger.error("evolution_log_promote_failed", error=str(exc))

    async def _document(
        self, pool: Any, generation: int, strategy_pool: StrategyPool, summary: dict,
    ) -> None:
        # / 11. DOCUMENT: generate report and update docs
        pool_summary = strategy_pool.summary()
        try:
            report = await generate_report(
                generation=generation,
                killed=summary["killed"],
                mutated=summary["mutated"],
                promoted=summary["promoted"],
                pool_summary=pool_summary,
            )
            report_path = str(REPORTS_DIR / f"evolution_gen_{generation}.md")
            await update_docs(generation, report_path)
        except Exception as exc:
            logger.error("report_generation_failed", error=str(exc))
            summary["errors"].append(f"report failed: {exc}")

    async def _spawn_tier2(
        self, pool: Any, strategy_pool: StrategyPool, generation: int,
    ) -> list[dict]:
        # / for each live sector/general strategy, check if any symbol has enough
        # / trades to warrant a per-symbol tweak (tier 2)
        # / handles both sector-specific and universe-wide (all_stocks) strategies
        from src.data.symbols import get_sector, FULL_UNIVERSE
        spawned = []
        for entry in strategy_pool.list_by_status("live"):
            config = entry.strategy.config
            if config.get("tier", "sector") != "sector":
                continue

            # / determine which symbols to check
            sector = config.get("sector")
            if sector:
                symbols_to_check = get_sector_symbols(sector)
            else:
                # / general strategy (all_stocks, all, etc) — check all symbols
                symbols_to_check = entry.strategy.resolve_universe() or FULL_UNIVERSE

            for symbol in symbols_to_check:
                try:
                    trade_count = await count_symbol_trades(pool, config["id"], symbol)
                except Exception:
                    continue
                if trade_count < self._tier2_spawn_trades:
                    continue
                # / infer sector from symbol if strategy doesn't have one
                sym_sector = sector or get_sector(symbol)
                if not sym_sector:
                    continue
                if self._has_tier2(strategy_pool, sym_sector, symbol):
                    continue

                new_config = self._clone_as_tier2(config, symbol, sym_sector)
                strategy_pool.add(ConfigDrivenStrategy(new_config), status="paper_trading")
                try:
                    save_config(new_config)
                    await store_evolution_log(
                        pool, generation, "spawn_tier2", new_config["id"],
                        config["id"], f"{symbol} has {trade_count} trades in sector {sym_sector}",
                    )
                except Exception as exc:
                    logger.error("spawn_tier2_log_failed", error=str(exc))
                spawned.append({"id": new_config["id"], "symbol": symbol, "sector": sym_sector})
                logger.info("tier2_spawned", symbol=symbol, sector=sym_sector, parent=config["id"])

        return spawned

    async def _graduate_tier3(
        self, pool: Any, strategy_pool: StrategyPool, generation: int,
    ) -> list[dict]:
        # / tier-2 strategies with enough trades + beating sector base -> tier 3
        graduated = []
        for entry in strategy_pool.list_by_status("live"):
            config = entry.strategy.config
            if config.get("tier") != "tweaked":
                continue
            symbol = config.get("symbol")
            if not symbol:
                continue

            try:
                total_trades = await count_all_symbol_trades(pool, symbol)
            except Exception:
                continue
            if total_trades < self._tier3_graduate_trades:
                continue

            sector_sharpe = self._get_sector_base_sharpe(strategy_pool, config.get("sector"))
            if entry.score and entry.score.sharpe_ratio > sector_sharpe + self._tier3_sharpe_delta:
                config["tier"] = "graduated"
                try:
                    save_config(config)
                    await store_evolution_log(
                        pool, generation, "graduate_tier3", config["id"],
                        config.get("parent_id"),
                        f"{symbol}: {total_trades} trades, sharpe {entry.score.sharpe_ratio:.2f} > sector {sector_sharpe:.2f} + {self._tier3_sharpe_delta}",
                    )
                except Exception as exc:
                    logger.error("graduate_tier3_log_failed", error=str(exc))
                graduated.append({"id": config["id"], "symbol": symbol})
                logger.info("tier3_graduated", symbol=symbol, strategy_id=config["id"])

        return graduated

    @staticmethod
    def _has_tier2(strategy_pool: StrategyPool, sector: str, symbol: str) -> bool:
        # / check if a tier-2 strategy already exists for this symbol in this sector
        for entry in strategy_pool.all_entries():
            c = entry.strategy.config
            if c.get("tier") in ("tweaked", "graduated") and c.get("symbol") == symbol and c.get("sector") == sector:
                if entry.status != "killed":
                    return True
        return False

    @staticmethod
    def _clone_as_tier2(sector_config: dict, symbol: str, sector: str | None = None) -> dict:
        # / create a tier-2 clone from a sector/general strategy for a specific symbol
        import copy
        import uuid
        new = copy.deepcopy(sector_config)
        new["id"] = f"strategy_{uuid.uuid4().hex[:8]}"
        new["parent_id"] = sector_config["id"]
        new["symbol"] = symbol
        new["sector"] = sector or sector_config.get("sector")
        new["tier"] = "tweaked"
        new["universe"] = symbol
        new["name"] = f"{sector_config.get('name', 'unknown')}_{symbol}"
        new["created_by"] = "evolution_tier2"
        new["version"] = 1
        if "metadata" not in new:
            new["metadata"] = {}
        new["metadata"]["status"] = "paper_trading"
        new["metadata"]["generation"] = sector_config.get("metadata", {}).get("generation", 0) + 1
        return new

    @staticmethod
    def _get_sector_base_sharpe(strategy_pool: StrategyPool, sector: str | None) -> float:
        # / get the best sharpe of tier-1 (sector) strategies in this sector
        if not sector:
            return 0.0
        best = 0.0
        for entry in strategy_pool.all_entries():
            c = entry.strategy.config
            if c.get("sector") == sector and c.get("tier", "sector") == "sector":
                if entry.score and entry.score.sharpe_ratio > best:
                    best = entry.score.sharpe_ratio
        return best
