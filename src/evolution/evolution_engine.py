# / karpathy autoresearch loop for strategy evolution
# / read -> rank -> kill -> mutate -> backtest -> score -> promote -> document

from __future__ import annotations

import asyncio
from typing import Any

import numpy as np
import structlog

from src.agents.tools import (
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
from src.strategies.strategy_pool import (
    StrategyPool,
    StrategyScore,
    compute_composite_score,
)

logger = structlog.get_logger(__name__)


class EvolutionEngine:
    def __init__(self, rng: np.random.Generator | None = None):
        self._rng = rng or np.random.default_rng()

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

        summary["generation"] = generation
        logger.info("evolution_start", generation=generation, pool_size=strategy_pool.size)

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

        logger.info("evolution_killed", count=len(killed_configs))

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
                else:
                    mutated_configs.append(result)

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

        # / 9. DOCUMENT: generate report and update docs
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

        notify_evolution_summary(summary)
        logger.info(
            "evolution_complete",
            generation=generation,
            killed=len(summary["killed"]),
            mutated=len(summary["mutated"]),
            promoted=len(summary["promoted"]),
        )
        return summary
