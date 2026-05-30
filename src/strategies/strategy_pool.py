
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import structlog

from .base_strategy import ConfigDrivenStrategy

logger = structlog.get_logger(__name__)


@dataclass
class StrategyScore:
    strategy_id: str
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    total_trades: int = 0
    total_pnl: float = 0.0
    brier_score: float | None = None
    sortino_ratio: float | None = None
    calmar_ratio: float | None = None
    composite_score: float = 0.0


@dataclass
class StrategyEntry:
    strategy: ConfigDrivenStrategy
    status: str = "backtest_pending"
    score: StrategyScore | None = None
    added_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    status_changed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def compute_composite_score(
    sharpe: float,
    win_rate: float,
    max_drawdown: float,
    brier: float | None = None,
) -> float:
    score = sharpe * 0.4 + win_rate * 0.3 - abs(max_drawdown) * 0.2
    if brier is not None:
        score += (0.25 - brier) * 0.1
    return score


class StrategyPool:
    def __init__(self):
        self._strategies: dict[str, StrategyEntry] = {}

    def add(
        self, strategy: ConfigDrivenStrategy, status: str = "backtest_pending",
        status_changed_at: datetime | None = None,
    ) -> None:
        if strategy.strategy_id in self._strategies:
            logger.warning("strategy_already_in_pool", id=strategy.strategy_id)
            return

        entry = StrategyEntry(strategy=strategy, status=status)
        if status_changed_at is not None:
            entry.status_changed_at = status_changed_at
        self._strategies[strategy.strategy_id] = entry
        logger.info("strategy_added", id=strategy.strategy_id, name=strategy.name, status=status)

    def remove(self, strategy_id: str) -> bool:
        if strategy_id in self._strategies:
            del self._strategies[strategy_id]
            logger.info("strategy_removed", id=strategy_id)
            return True
        return False

    def update_status(self, strategy_id: str, new_status: str) -> bool:
        valid_statuses = ("backtest_pending", "backtesting", "paper_trading", "promoted", "live", "killed")
        if new_status not in valid_statuses:
            raise ValueError(f"invalid status: {new_status}, must be one of {valid_statuses}")

        entry = self._strategies.get(strategy_id)
        if entry is None:
            return False

        old_status = entry.status
        entry.status = new_status
        entry.status_changed_at = datetime.now(timezone.utc)
        logger.info("strategy_status_changed", id=strategy_id, old=old_status, new=new_status)
        return True

    def update_score(self, strategy_id: str, score: StrategyScore) -> bool:
        entry = self._strategies.get(strategy_id)
        if entry is None:
            return False

        score.composite_score = compute_composite_score(
            sharpe=score.sharpe_ratio,
            win_rate=score.win_rate,
            max_drawdown=score.max_drawdown,
            brier=score.brier_score,
        )
        entry.score = score
        return True

    def get(self, strategy_id: str) -> StrategyEntry | None:
        return self._strategies.get(strategy_id)

    def get_strategy(self, strategy_id: str) -> ConfigDrivenStrategy | None:
        entry = self._strategies.get(strategy_id)
        return entry.strategy if entry else None

    def all_entries(self) -> list[StrategyEntry]:
        return list(self._strategies.values())

    def list_by_status(self, status: str) -> list[StrategyEntry]:
        # / promoted/live treated as synonyms
        if status == "promoted":
            return [e for e in self._strategies.values() if e.status in ("promoted", "live")]
        if status == "live":
            return [e for e in self._strategies.values() if e.status in ("promoted", "live")]
        return [e for e in self._strategies.values() if e.status == status]

    def ranked(self, status: str | None = None) -> list[StrategyEntry]:
        # / optionally filter by status
        entries = list(self._strategies.values())
        if status:
            entries = [e for e in entries if e.status == status]
        return sorted(
            entries,
            key=lambda e: e.score.composite_score if e.score else float("-inf"),
            reverse=True,
        )

    def bottom_quartile(self, status: str | None = None) -> list[StrategyEntry]:
        ranked = self.ranked(status=status)
        if len(ranked) < 4:
            return []
        cutoff = max(1, len(ranked) // 4)
        return ranked[-cutoff:]

    def top_performers(self, n: int = 5, status: str | None = None) -> list[StrategyEntry]:
        ranked = self.ranked(status=status)
        return ranked[:n]

    @property
    def size(self) -> int:
        return len(self._strategies)

    @property
    def active_count(self) -> int:
        return sum(1 for e in self._strategies.values() if e.status in ("paper_trading", "promoted", "live"))

    def summary(self) -> dict[str, Any]:
        # / pool summary for logging/reporting
        by_status: dict[str, int] = {}
        for entry in self._strategies.values():
            by_status[entry.status] = by_status.get(entry.status, 0) + 1

        top = self.ranked()[:3]
        top_summary = [
            {"id": e.strategy.strategy_id, "score": e.score.composite_score if e.score else None}
            for e in top
        ]

        return {
            "total": self.size,
            "active": self.active_count,
            "by_status": by_status,
            "top_3": top_summary,
        }
