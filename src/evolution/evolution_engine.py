# / karpathy autoresearch loop for strategy evolution
# / read -> rank -> kill -> mutate -> backtest -> score -> promote -> document

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any

import numpy as np
import structlog

from src.agents.data_tools import fire_and_forget
from src.data.strategy_metrics import (
    fetch_strategy_scores,
    store_evolution_log,
    store_strategy_score,
)
from src.data.trade_history import count_all_symbol_trades, fetch_recent_trades
from src.data.symbols import get_sector_symbols
from src.evolution.documentation import update_docs
from src.evolution.report_generator import REPORTS_DIR, generate_report
from src.evolution.strategy_mutator import mutate_strategy
from src.knowledge.db_helpers import (
    store_evolution_mutation,
    update_evolution_mutation_by_mutant,
    update_evolution_mutation_outcome,
)
from src.knowledge.strategy_lessons import StrategyLessons
from src.knowledge.wiki_context import WikiContext
from src.notifications.notifier import notify_evolution_summary, notify_strategy_promoted
from src.strategies.backtest import BacktestResult, run_backtest
from src.strategies.base_strategy import ConfigDrivenStrategy
from src.strategies.strategy_loader import save_config
from src.strategies.strategy_pool import (
    StrategyPool,
    StrategyScore,
    compute_composite_score,
)

logger = structlog.get_logger(__name__)

# / default 80% guided / 20% unguided, env override WIKI_GUIDED_RATIO
DEFAULT_WIKI_GUIDED_RATIO = 0.80


def _broadcast_status_change(strategy_id: str, old: str | None, new: str,
                             reason: str | None = None) -> None:
    # / notify ws clients when a strategy changes status
    # / (killed, paper_trading, live). late-bind so headless tests pass.
    try:
        from src.dashboard.app import _ws_clients, broadcast
    except Exception:
        return
    if not _ws_clients:
        return
    try:
        fire_and_forget(broadcast("strategy_status_change", {
            "strategy_id": strategy_id,
            "old_status": old,
            "new_status": new,
            "reason": reason,
        }))
    except Exception as exc:
        logger.debug("broadcast_status_change_failed", error=str(exc)[:120])


class EvolutionEngine:
    def __init__(self, rng: np.random.Generator | None = None, risk_limits: dict | None = None):
        self._rng = rng or np.random.default_rng()
        rl = risk_limits or {}
        evo = rl.get("evolution", {})
        self._tier2_spawn_trades = evo.get("tier2_spawn_trades", 20)
        self._tier2_kill_trades = evo.get("tier2_kill_trades", 20)
        self._tier3_graduate_trades = evo.get("tier3_graduate_trades", 100)
        self._tier3_sharpe_delta = evo.get("tier3_sharpe_delta", 0.2)
        self._tier2_param_freedom = evo.get("tier2_param_freedom", 0.20)
        # / promotion gates from risk_limits.json (were hardcoded 14/0.8 with no win_rate)
        self._paper_trade_min_days = int(rl.get("paper_trade_min_days", 14))
        self._promotion_min_sharpe = float(rl.get("promotion_min_sharpe", 0.8))
        self._promotion_min_win_rate = float(rl.get("promotion_min_win_rate", 0.45))
        try:
            self._wiki_guided_ratio = float(
                os.environ.get("WIKI_GUIDED_RATIO", DEFAULT_WIKI_GUIDED_RATIO),
            )
        except (TypeError, ValueError):
            self._wiki_guided_ratio = DEFAULT_WIKI_GUIDED_RATIO
        # / clamp to [0, 1]
        self._wiki_guided_ratio = max(0.0, min(1.0, self._wiki_guided_ratio))
        self._current_regime: str | None = None

    async def run(
        self,
        pool: Any,
        strategy_pool: StrategyPool,
        market_data: dict | None = None,
        regime: str | None = None,
    ) -> dict[str, Any]:
        # / main evolution loop
        # / pool = asyncpg connection pool for db operations
        # / strategy_pool = in-memory strategy pool
        # / market_data = historical data for backtesting mutations
        # / regime = optional current regime hint for wiki context assembly

        self._current_regime = regime
        self._generation: int = 0

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
        self._generation = generation
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

        # / 13. LOG CYCLE: ensure every evolution run writes at least one log row
        # / dashboard queries MAX(created_at) from evolution_log to show "last evolution"
        try:
            await store_evolution_log(
                pool, generation, "cycle_complete", "system", None,
                f"evolution cycle: {len(summary['killed'])} killed, {len(summary['mutated'])} mutated, {len(summary['promoted'])} promoted",
            )
        except Exception as exc:
            logger.error("evolution_log_cycle_failed", error=str(exc))

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
        # / 2. RANK + 3. KILL: bottom quartile + dormancy fallback
        # / skip quartile kill if strategies haven't accumulated enough trade data
        scored_count = sum(
            1 for e in strategy_pool.ranked()
            if e.score and e.score.total_trades >= 3
        )
        if scored_count < 3:
            logger.info(
                "evolution_skip_quartile_kill",
                reason="not enough strategies with >=3 trades",
                scored=scored_count, pool_size=strategy_pool.size,
            )
            bottom = []
        else:
            bottom = strategy_pool.bottom_quartile()

        # / bug e2 clean slate: dormancy kill is GATED until the system accumulates at least
        # / 10 closed trades across any strategy. prior historical "dormancy" is polluted by
        # / broken intraday + broken consensus gates that were fixed in phase e — killing
        # / strategies off that data would erase work that never got a fair run. once we have
        # / real trade data from the fixed pipeline, the gate flips and dormancy kicks in.
        bottom_ids = {e.strategy.strategy_id for e in bottom}
        dormant: list = []
        total_closed_trades = sum(
            (e.score.total_trades if e.score else 0)
            for e in strategy_pool.ranked()
        )
        clean_slate_enabled = total_closed_trades >= 10
        if not clean_slate_enabled:
            logger.info(
                "evolution_dormancy_gated_clean_slate",
                total_closed_trades=total_closed_trades,
                reason="system needs >=10 closed trades before dormancy kills engage",
            )
        else:
            for entry in strategy_pool.ranked():
                if entry.strategy.strategy_id in bottom_ids or entry.status == "killed":
                    continue
                days_alive = (datetime.now(timezone.utc) - entry.status_changed_at).days
                trade_count = entry.score.total_trades if entry.score else 0
                if days_alive >= 30 and trade_count == 0:
                    dormant.append(entry)
                    logger.info(
                        "evolution_dormancy_kill_candidate",
                        strategy_id=entry.strategy.strategy_id, days=days_alive, trades=trade_count,
                    )

        killed_configs: list[dict] = []
        for entry in list(bottom) + dormant:
            sid = entry.strategy.strategy_id
            config = entry.strategy.config
            if entry in dormant:
                days_alive = (datetime.now(timezone.utc) - entry.status_changed_at).days
                reason = f"dormant (alive {days_alive}d, 0 trades)"
            else:
                reason = "bottom quartile"
                if entry.score:
                    reason += f" (composite={entry.score.composite_score:.4f})"

            prev_status = entry.status
            strategy_pool.update_status(sid, "killed")
            # / persist kill to disk so restart doesn't resurrect the strategy
            config.setdefault("metadata", {})["status"] = "killed"
            try:
                save_config(config)
            except Exception as exc:
                # / save_config silently failed before; now loudly notify so stale configs get surfaced
                logger.error("kill_save_config_failed", strategy_id=sid, error=str(exc))
                try:
                    from src.notifications.notifier import notify_system_error
                    notify_system_error(
                        f"kill_save_config_failed strategy={sid}: {str(exc)[:100]}", "evolution",
                    )
                except Exception:
                    pass
            _broadcast_status_change(sid, prev_status, "killed", reason=reason)
            killed_configs.append({"id": sid, "config": config, "reason": reason})
            summary["killed"].append({"id": sid, "reason": reason})

            try:
                await store_evolution_log(
                    pool, generation, "kill", sid,
                    config.get("parent_id"), reason,
                )
            except Exception as exc:
                logger.error("evolution_log_kill_failed", strategy_id=sid, error=str(exc))

            # / flag the evolution_mutations row (if any) as not survived
            try:
                await update_evolution_mutation_by_mutant(
                    pool, mutant_strategy_id=sid, survived=False,
                )
            except Exception as exc:
                logger.info("evo_mutation_killed_update_failed", error=str(exc)[:120])

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
                prev_status = entry.status
                strategy_pool.update_status(sid, "killed")
                _broadcast_status_change(sid, prev_status, "killed", reason=reason)
                # / persist kill to disk (same pattern as _kill_bottom_quartile)
                config.setdefault("metadata", {})["status"] = "killed"
                try:
                    save_config(config)
                except Exception as exc:
                    logger.error("tier2_kill_save_failed", strategy_id=sid, error=str(exc))
                    try:
                        from src.notifications.notifier import notify_system_error
                        notify_system_error(
                            f"tier2_kill_save_failed strategy={sid}: {str(exc)[:100]}", "evolution",
                        )
                    except Exception:
                        pass
                killed_configs.append({"id": sid, "config": config, "reason": reason})
                summary["killed"].append({"id": sid, "reason": reason})
                try:
                    await store_evolution_log(pool, generation, "kill", sid, config.get("parent_id"), reason)
                except Exception as exc:
                    logger.error("evolution_log_tier2_kill_failed", error=str(exc))

                try:
                    await update_evolution_mutation_by_mutant(
                        pool, mutant_strategy_id=sid, survived=False,
                    )
                except Exception as exc:
                    logger.info("evo_mutation_tier2_killed_update_failed", error=str(exc)[:120])

    async def _mutate_killed(
        self, pool: Any, killed_configs: list[dict], strategy_pool: StrategyPool,
        summary: dict,
    ) -> list[dict]:
        # / 4. MUTATE: propose mutations for each killed strategy
        top_performers = strategy_pool.top_performers(n=1)
        top_config = top_performers[0].strategy.config if top_performers else {}

        wc = WikiContext(pool)
        mutation_tasks: list = []
        mutation_meta: list[dict] = []
        for killed in killed_configs:
            sid = killed["id"]
            try:
                trades = await fetch_recent_trades(pool, strategy_id=sid, limit=10)
            except Exception:
                trades = []

            # / 80/20 wiki-guided split (env override via WIKI_GUIDED_RATIO)
            wiki_guided = bool(self._rng.random() < self._wiki_guided_ratio)
            wiki_ctx: str | None = None
            if wiki_guided:
                try:
                    wiki_ctx = await wc.get_mutation_context(
                        sid,
                        killed_config=killed["config"],
                        top_config=top_config,
                        regime=self._current_regime,
                    )
                except Exception as exc:
                    logger.info(
                        "wiki_context_fetch_failed",
                        strategy_id=sid, error=str(exc)[:120],
                    )
                    wiki_ctx = None
                    wiki_guided = False

            ctx_tokens = len(wiki_ctx or "") // 4

            row_id: int | None = None
            try:
                row_id = await store_evolution_mutation(
                    pool,
                    generation=self._generation,
                    parent_strategy_id=sid,
                    wiki_guided=wiki_guided,
                    wiki_context_tokens=ctx_tokens,
                )
            except Exception as exc:
                logger.info(
                    "store_evolution_mutation_failed",
                    strategy_id=sid, error=str(exc)[:120],
                )

            # / capture parent sharpe for later delta computation
            parent_entry = strategy_pool.get(sid)
            parent_sharpe: float | None = None
            if parent_entry and parent_entry.score:
                parent_sharpe = float(parent_entry.score.sharpe_ratio)

            mutation_tasks.append(
                mutate_strategy(
                    killed["config"], top_config, trades,
                    rng=self._rng, wiki_context=wiki_ctx,
                )
            )
            mutation_meta.append({
                "parent_id": sid,
                "row_id": row_id,
                "wiki_guided": wiki_guided,
                "parent_sharpe": parent_sharpe,
            })

        mutated_configs: list[dict] = []
        if mutation_tasks:
            results = await asyncio.gather(*mutation_tasks, return_exceptions=True)
            for meta, result in zip(mutation_meta, results, strict=False):
                if isinstance(result, Exception):
                    logger.error(
                        "mutation_failed",
                        parent_id=meta["parent_id"], error=str(result),
                    )
                    summary["errors"].append(f"mutation failed: {result}")
                    continue
                configs = result if isinstance(result, list) else [result]
                # / first child binds to the pre-inserted row; clone tracking row for extras
                for idx, cfg in enumerate(configs):
                    if not isinstance(cfg, dict):
                        continue
                    cfg["_evo_mutation_row_id"] = meta["row_id"] if idx == 0 else None
                    cfg["_evo_parent_id"] = meta["parent_id"]
                    cfg["_evo_parent_sharpe"] = meta["parent_sharpe"]
                    cfg["_evo_wiki_guided"] = meta["wiki_guided"]
                    mutated_configs.append(cfg)
                    if meta["row_id"] is not None and idx == 0:
                        try:
                            await update_evolution_mutation_outcome(
                                pool,
                                row_id=meta["row_id"],
                                mutant_strategy_id=cfg.get("id"),
                                parent_sharpe=meta["parent_sharpe"],
                            )
                        except Exception as exc:
                            logger.info(
                                "update_evolution_mutation_outcome_failed",
                                error=str(exc)[:120],
                            )

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
            for config, bt_result in zip(mutated_configs, bt_results, strict=False):
                if isinstance(bt_result, BaseException):
                    logger.error("backtest_failed", strategy_id=config.get("id"), error=str(bt_result))
                    summary["errors"].append(f"backtest failed for {config.get('id')}: {bt_result}")
                else:
                    # / walk-forward out-of-sample validation (reject overfit mutations)
                    # / only check overfitting when IS sharpe is positive — negative IS
                    # / strategies pass through to the median scoring filter instead
                    try:
                        is_sharpe = bt_result.sharpe_ratio
                        if is_sharpe > 0:
                            from src.strategies.walk_forward import walk_forward_test
                            wf_result = await walk_forward_test(ConfigDrivenStrategy(config), market_data)
                            oos_sharpe = wf_result.avg_oos_sharpe
                            degradation = 1 - (oos_sharpe / is_sharpe)
                            if oos_sharpe < 0.3 or degradation > 0.5:
                                logger.info("walk_forward_rejected", strategy_id=config.get("id"), oos_sharpe=round(oos_sharpe, 3), degradation=round(degradation, 3))
                                continue
                    except Exception as exc:
                        logger.debug("walk_forward_skipped", error=str(exc)[:100])
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

        lessons = StrategyLessons(pool)
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

            # / patch the evolution_mutations row with mutant sharpe + delta
            evo_row_id = config.get("_evo_mutation_row_id")
            parent_sharpe = config.get("_evo_parent_sharpe")
            mutant_sharpe = float(bt_result.sharpe_ratio)
            sharpe_delta: float | None = None
            if parent_sharpe is not None:
                sharpe_delta = round(mutant_sharpe - float(parent_sharpe), 4)
            if evo_row_id is not None:
                try:
                    await update_evolution_mutation_outcome(
                        pool,
                        row_id=int(evo_row_id),
                        mutant_sharpe=mutant_sharpe,
                        sharpe_delta=sharpe_delta,
                        parent_sharpe=parent_sharpe,
                    )
                except Exception as exc:
                    logger.info(
                        "evo_mutation_score_update_failed",
                        error=str(exc)[:120],
                    )
            else:
                # / fallback: match by mutant id (second-child case)
                try:
                    await update_evolution_mutation_by_mutant(
                        pool,
                        mutant_strategy_id=config.get("id", ""),
                        mutant_sharpe=mutant_sharpe,
                        sharpe_delta=sharpe_delta,
                        parent_sharpe=parent_sharpe,
                    )
                except Exception as exc:
                    logger.info(
                        "evo_mutation_score_update_by_mutant_failed",
                        error=str(exc)[:120],
                    )

            # / record a mutation_result lesson on the parent strategy
            parent_sid = config.get("_evo_parent_id") or config.get("parent_id")
            if parent_sid:
                try:
                    guided = config.get("_evo_wiki_guided", False)
                    summary_text = (
                        f"Mutation {config.get('id', '?')} composite={composite:.4f} "
                        f"sharpe={mutant_sharpe:.3f}"
                    )
                    if sharpe_delta is not None:
                        summary_text += f" delta={sharpe_delta:+.3f}"
                    summary_text += f" wiki_guided={guided}"
                    await lessons.record(
                        parent_sid,
                        "mutation_result",
                        summary_text,
                        context={
                            "mutant_id": config.get("id"),
                            "composite": composite,
                            "mutant_sharpe": mutant_sharpe,
                            "sharpe_delta": sharpe_delta,
                            "wiki_guided": guided,
                        },
                        trade_count=0,
                    )
                except Exception as exc:
                    logger.info("lesson_record_failed", error=str(exc)[:120])

            # / strip the private _evo_* bookkeeping from the persisted config
            for _k in ("_evo_mutation_row_id", "_evo_parent_id", "_evo_parent_sharpe", "_evo_wiki_guided"):
                config.pop(_k, None)

            if composite > median_score:
                # / add to pool as paper_trading
                config["metadata"]["status"] = "paper_trading"
                strategy = ConfigDrivenStrategy(config)
                strategy_pool.add(strategy, status="paper_trading")
                _broadcast_status_change(config["id"], None, "paper_trading",
                                         reason=f"mutant composite={composite:.3f}")

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
        # / 8. PROMOTE: paper_trading strategies must clear ALL risk_limits gates
        paper_strategies = strategy_pool.list_by_status("paper_trading")
        for entry in paper_strategies:
            # / compute paper_days dynamically from status_changed_at
            paper_days = (datetime.now(timezone.utc) - entry.status_changed_at).days
            if not entry.score:
                continue
            sharpe = entry.score.sharpe_ratio
            win_rate = entry.score.win_rate
            if (
                paper_days >= self._paper_trade_min_days
                and sharpe >= self._promotion_min_sharpe
                and win_rate >= self._promotion_min_win_rate
            ):
                sid = entry.strategy.strategy_id
                strategy_pool.update_status(sid, "live")
                _broadcast_status_change(sid, "paper_trading", "live",
                                         reason=f"sharpe={sharpe:.2f}, days={paper_days}")
                summary["promoted"].append({"id": sid})
                notify_strategy_promoted(sid, sharpe, paper_days)
                logger.info(
                    "strategy_promoted",
                    strategy_id=sid, sharpe=sharpe, win_rate=win_rate, days=paper_days,
                )

                try:
                    await store_evolution_log(
                        pool, generation, "promote", sid,
                        entry.strategy.config.get("parent_id"),
                        f"paper_trading {paper_days}d, sharpe={sharpe:.2f}, win_rate={win_rate:.2f}",
                    )
                except Exception as exc:
                    logger.error("evolution_log_promote_failed", error=str(exc))

                # / flag the mutation row as survived
                try:
                    await update_evolution_mutation_by_mutant(
                        pool, mutant_strategy_id=sid, survived=True,
                    )
                except Exception as exc:
                    logger.info("evo_mutation_survived_update_failed", error=str(exc)[:120])

    async def _document(
        self, pool: Any, generation: int, strategy_pool: StrategyPool, summary: dict,
    ) -> None:
        # / 11. DOCUMENT: generate report and update docs
        pool_summary = strategy_pool.summary()
        try:
            await generate_report(
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
        # / for live sector/general strategies, check per-symbol trade counts
        # / single grouped query replaces N*M acquires
        from src.data.symbols import FULL_UNIVERSE, get_sector
        spawned = []

        # / collect candidate (strategy, symbols) pairs
        candidates: list[tuple[Any, dict, str | None, list[str]]] = []
        all_strat_ids: set[str] = set()
        all_symbols: set[str] = set()
        for entry in strategy_pool.list_by_status("live"):
            config = entry.strategy.config
            if config.get("tier", "sector") != "sector":
                continue
            sector = config.get("sector")
            if sector:
                symbols_to_check = list(get_sector_symbols(sector))
            else:
                symbols_to_check = list(entry.strategy.resolve_universe() or FULL_UNIVERSE)
            candidates.append((entry, config, sector, symbols_to_check))
            all_strat_ids.add(config["id"])
            all_symbols.update(symbols_to_check)

        # / one grouped query for all (strategy_id, symbol) trade counts
        counts: dict[tuple[str, str], int] = {}
        if all_strat_ids and all_symbols:
            try:
                async with pool.acquire() as conn:
                    rows = await conn.fetch(
                        """SELECT strategy_id, symbol, COUNT(*) AS n FROM trade_log
                        WHERE strategy_id = ANY($1::text[])
                          AND symbol = ANY($2::text[])
                        GROUP BY strategy_id, symbol""",
                        list(all_strat_ids), list(all_symbols),
                    )
                counts = {(r["strategy_id"], r["symbol"]): int(r["n"]) for r in rows}
            except Exception as exc:
                logger.warning("tier2_count_batch_failed", error=str(exc))

        for entry, config, sector, symbols_to_check in candidates:
            for symbol in symbols_to_check:
                trade_count = counts.get((config["id"], symbol), 0)
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
            if (c.get("tier") in ("tweaked", "graduated")
                    and c.get("symbol") == symbol
                    and c.get("sector") == sector
                    and entry.status != "killed"):
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
            if (c.get("sector") == sector
                    and c.get("tier", "sector") == "sector"
                    and entry.score and entry.score.sharpe_ratio > best):
                best = entry.score.sharpe_ratio
        return best
