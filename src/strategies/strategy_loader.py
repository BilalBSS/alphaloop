# / loads strategy configs from json files, validates, creates strategy objects
# / evolution engine creates/mutates json files — this just reads them
# / pydantic validation ensures configs are sane before instantiation

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel, field_validator, model_validator

from .base_strategy import ConfigDrivenStrategy

logger = structlog.get_logger(__name__)

CONFIGS_DIR = Path(__file__).parent.parent.parent / "configs" / "strategies"


class SignalConfig(BaseModel):
    indicator: str
    condition: str
    period: int | None = None
    lookback: int | None = None
    threshold: float | None = None
    std_dev: float | None = None
    multiplier: float | None = None
    level: float | None = None  # / fibonacci level (0.236, 0.382, etc)


class EntryConditionsConfig(BaseModel):
    operator: str = "AND"
    signals: list[SignalConfig]

    @field_validator("operator")
    @classmethod
    def validate_operator(cls, v: str) -> str:
        if v not in ("AND", "OR"):
            raise ValueError(f"operator must be AND or OR, got {v}")
        return v

    @field_validator("signals")
    @classmethod
    def validate_signals_nonempty(cls, v: list[SignalConfig]) -> list[SignalConfig]:
        if not v:
            raise ValueError("at least one entry signal required")
        return v


class StopLossConfig(BaseModel):
    type: str = "fixed_pct"
    pct: float | None = None
    multiplier: float | None = None
    period: int | None = None


class TakeProfitConfig(BaseModel):
    indicator: str | None = None
    condition: str | None = None
    pct: float | None = None
    lookback: int | None = None
    period: int | None = None
    std_dev: float | None = None


class TimeExitConfig(BaseModel):
    max_holding_days: int


class ExitConditionsConfig(BaseModel):
    stop_loss: StopLossConfig
    take_profit: TakeProfitConfig | None = None
    time_exit: TimeExitConfig | None = None


class PositionSizingConfig(BaseModel):
    method: str = "fixed_pct"
    max_position_pct: float = 0.08
    kelly_fraction: float | None = None

    @field_validator("max_position_pct")
    @classmethod
    def validate_max_pct(cls, v: float) -> float:
        if v <= 0 or v > 0.10:
            raise ValueError(f"max_position_pct must be (0, 0.10], got {v}")
        return v


class FundamentalFiltersConfig(BaseModel):
    pe_ratio_max: float | None = None
    pe_vs_sector: str | None = None
    revenue_growth_min: float | None = None
    fcf_margin_min: float | None = None
    debt_to_equity_max: float | None = None
    dcf_upside_min: float | None = None
    insider_buying_recent: bool | None = None
    # / crypto-specific filters (phase 8)
    nvt_max: float | None = None
    funding_rate_max: float | None = None
    news_sentiment_min: float | None = None


class StrategyMetadata(BaseModel):
    generation: int = 1
    status: str = "backtest_pending"
    backtest_sharpe: float | None = None
    backtest_max_drawdown: float | None = None
    backtest_win_rate: float | None = None
    brier_score: float | None = None
    paper_trade_days: int | None = None
    paper_trade_pnl: float | None = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        valid = ("backtest_pending", "backtesting", "paper_trading", "live", "killed")
        if v not in valid:
            raise ValueError(f"status must be one of {valid}, got {v}")
        return v


class StrategyConfig(BaseModel):
    id: str
    name: str
    version: int = 1
    created_by: str = "human"
    parent_id: str | None = None
    description: str = ""
    asset_class: str = "stocks"
    universe: str  # / universe reference: "all", "all_stocks", "all_crypto", or comma-separated symbols
    sector: str | None = None   # / sector grouping for hierarchical evolution
    symbol: str | None = None   # / single-symbol targeting (tier 2+)
    tier: str = "sector"        # / "sector", "tweaked", "graduated"
    fundamental_filters: FundamentalFiltersConfig | None = None
    entry_conditions: EntryConditionsConfig
    exit_conditions: ExitConditionsConfig
    position_sizing: PositionSizingConfig = PositionSizingConfig()
    metadata: StrategyMetadata = StrategyMetadata()
    bypass_consensus: bool = False           # / skip ai consensus gate (for pipeline testing)
    signal_threshold_override: float | None = None  # / override SIGNAL_THRESHOLD per-strategy

    @field_validator("universe", mode="before")
    @classmethod
    def validate_universe(cls, v: str | list[str]) -> str:
        # / accept both string refs ("all", "all_stocks") and legacy list format
        if isinstance(v, list):
            if not v:
                raise ValueError("universe must not be empty")
            return ",".join(v)
        if not v or not v.strip():
            raise ValueError("universe must not be empty")
        return v

    @field_validator("asset_class")
    @classmethod
    def validate_asset_class(cls, v: str) -> str:
        if v not in ("stocks", "crypto", "mixed"):
            raise ValueError(f"asset_class must be stocks/crypto/mixed, got {v}")
        return v

    @field_validator("tier")
    @classmethod
    def validate_tier(cls, v: str) -> str:
        if v not in ("sector", "tweaked", "graduated"):
            raise ValueError(f"tier must be sector/tweaked/graduated, got {v}")
        return v

    @field_validator("sector")
    @classmethod
    def validate_sector(cls, v: str | None) -> str | None:
        if v is not None:
            from src.data.symbols import SECTORS
            if v not in SECTORS:
                raise ValueError(f"sector must be one of {list(SECTORS.keys())}, got {v}")
        return v

    @model_validator(mode="after")
    def validate_tier_constraints(self) -> "StrategyConfig":
        # / tier-2 and tier-3 require a symbol
        if self.tier in ("tweaked", "graduated") and not self.symbol:
            raise ValueError(f"tier '{self.tier}' requires a symbol to be set")
        # / tier-1 (sector) cannot have a symbol
        if self.tier == "sector" and self.symbol:
            raise ValueError("sector-tier strategies cannot target a single symbol")
        return self

    @model_validator(mode="after")
    def validate_track_constraints(self) -> "StrategyConfig":
        # / fundamental-gated = at least one filter field is actually set
        # / empty {} or all-None fields = momentum-only (no free 8% bypass)
        has_fundamentals = (
            self.fundamental_filters is not None
            and any(
                v is not None
                for v in self.fundamental_filters.model_dump().values()
            )
        )
        num_signals = len(self.entry_conditions.signals)

        if has_fundamentals and num_signals < 2:
            raise ValueError(
                f"fundamental-gated strategies need at least 2 technical signals, got {num_signals}"
            )
        if not has_fundamentals and num_signals < 1:
            raise ValueError("momentum-only strategies need at least 1 technical signal")
        if num_signals > 8:
            raise ValueError(f"max 8 entry conditions (overfitting risk), got {num_signals}")

        # / position sizing caps per track
        max_pct = self.position_sizing.max_position_pct
        if has_fundamentals and max_pct > 0.08:
            raise ValueError(f"fundamental-gated max position is 8%, got {max_pct:.0%}")
        if not has_fundamentals and max_pct > 0.04:
            raise ValueError(f"momentum-only max position is 4%, got {max_pct:.0%}")

        return self


def validate_config(raw: dict[str, Any]) -> StrategyConfig:
    # / validate a raw dict against the strategy config schema
    return StrategyConfig(**raw)


def load_config_file(path: Path) -> ConfigDrivenStrategy:
    # / load and validate a single strategy config file
    with open(path) as f:
        raw = json.load(f)

    config = validate_config(raw)
    logger.info("strategy_loaded", id=config.id, name=config.name, path=str(path))
    # / use validated/normalized dict (e.g. universe list -> string coercion)
    return ConfigDrivenStrategy(config.model_dump())


def load_all_configs(
    directory: Path | None = None,
    status_filter: set[str] | None = None,
) -> list[ConfigDrivenStrategy]:
    # / load all strategy configs from a directory
    # / optionally filter by metadata.status
    config_dir = directory or CONFIGS_DIR
    if not config_dir.exists():
        logger.warning("strategy_config_dir_missing", path=str(config_dir))
        return []

    strategies: list[ConfigDrivenStrategy] = []
    for path in sorted(config_dir.glob("*.json")):
        try:
            strategy = load_config_file(path)
            if status_filter:
                status = strategy.config.get("metadata", {}).get("status", "")
                if status not in status_filter:
                    continue
            strategies.append(strategy)
        except (json.JSONDecodeError, ValueError, KeyError, TypeError, OSError) as e:
            logger.error("strategy_config_load_error", path=str(path), error=str(e))

    logger.info("strategies_loaded", count=len(strategies), directory=str(config_dir))
    return strategies


_SAFE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


def save_config(config: dict[str, Any], directory: Path | None = None) -> Path:
    # / save a strategy config to a json file
    # / used by evolution engine to persist mutations
    # / validates before writing to prevent persisting invalid configs
    validate_config(config)

    config_dir = directory or CONFIGS_DIR
    config_dir.mkdir(parents=True, exist_ok=True)

    strategy_id = config.get("id", "unknown")
    if not _SAFE_ID_PATTERN.match(strategy_id):
        raise ValueError(f"strategy id must be alphanumeric/underscore/hyphen only, got: {strategy_id}")
    path = config_dir / f"{strategy_id}.json"
    with open(path, "w") as f:
        json.dump(config, f, indent=2)

    logger.info("strategy_config_saved", id=strategy_id, path=str(path))
    return path
