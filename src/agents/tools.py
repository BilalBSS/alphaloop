# / re-export shim — kept for backwards compat with `from src.agents import tools`
# / actual implementations live in domain-specific modules:
# /   trade_tools, position_tools, sync_tools, market_tools, data_tools (agents/)
# /   strategy_metrics, synthesis, trade_history (data/, fixes layer violations)

from __future__ import annotations

from src.agents.data_tools import (
    _BG_TASKS,
    dict_to_analysis_data,
    fetch_analysis_score,
    fire_and_forget,
    log_event,
    log_observation,
    store_analysis_score,
)
from src.agents.market_tools import (
    fetch_avg_volume,
    fetch_close_history_batch,
    fetch_daily_ohlcv,
    fetch_intraday_ohlcv,
    fetch_latest_regime,
    fetch_peak_equity,
    fetch_recent_pnl,
    fetch_symbol_beta,
    store_computed_indicators,
    store_ict_indicators,
    store_peak_equity,
)
from src.agents.position_tools import (
    close_strategy_position,
    fetch_most_recent_open_entry,
    get_strategy_positions,
    mark_partial_exit_fired,
    open_strategy_position,
    reconcile_strategy_positions,
    sync_strategy_positions_from_alpaca,
)
from src.agents.sync_tools import (
    backfill_trade_pnl,
    get_sync_skipped_orders,
    sync_trades_from_alpaca,
)
from src.agents.trade_tools import (
    _STATUS_TABLES,
    attach_broker_order_id,
    claim_approved_trade_atomic,
    count_pending_signals_for_strategy,
    count_today_approved_trades,
    count_today_approved_trades_for_strategy,
    fetch_approved_trade_by_id,
    fetch_pending_signal_by_id,
    fetch_pending_signals,
    fetch_pending_trades,
    fetch_strategy_id_by_order,
    store_approved_trade,
    store_trade_log,
    store_trade_signal,
    update_trade_status,
)
from src.data.strategy_metrics import (
    fetch_strategy_scores,
    store_evolution_log,
    store_strategy_evaluation,
    store_strategy_score,
)
from src.data.synthesis import fetch_daily_synthesis, store_daily_synthesis
from src.data.trade_history import (
    count_all_symbol_trades,
    count_symbol_trades,
    fetch_recent_trades,
)

__all__ = [
    "_BG_TASKS",
    "_STATUS_TABLES",
    "attach_broker_order_id",
    "backfill_trade_pnl",
    "claim_approved_trade_atomic",
    "close_strategy_position",
    "count_all_symbol_trades",
    "count_pending_signals_for_strategy",
    "count_symbol_trades",
    "count_today_approved_trades",
    "count_today_approved_trades_for_strategy",
    "dict_to_analysis_data",
    "fetch_analysis_score",
    "fetch_approved_trade_by_id",
    "fetch_avg_volume",
    "fetch_close_history_batch",
    "fetch_daily_ohlcv",
    "fetch_daily_synthesis",
    "fetch_intraday_ohlcv",
    "fetch_latest_regime",
    "fetch_most_recent_open_entry",
    "fetch_peak_equity",
    "fetch_pending_signal_by_id",
    "fetch_pending_signals",
    "fetch_pending_trades",
    "fetch_recent_pnl",
    "fetch_recent_trades",
    "fetch_strategy_id_by_order",
    "fetch_strategy_scores",
    "fetch_symbol_beta",
    "fire_and_forget",
    "get_strategy_positions",
    "get_sync_skipped_orders",
    "log_event",
    "log_observation",
    "mark_partial_exit_fired",
    "open_strategy_position",
    "reconcile_strategy_positions",
    "store_analysis_score",
    "store_approved_trade",
    "store_computed_indicators",
    "store_daily_synthesis",
    "store_evolution_log",
    "store_ict_indicators",
    "store_peak_equity",
    "store_strategy_evaluation",
    "store_strategy_score",
    "store_trade_log",
    "store_trade_signal",
    "sync_strategy_positions_from_alpaca",
    "sync_trades_from_alpaca",
    "update_trade_status",
]
