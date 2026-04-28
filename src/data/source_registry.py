# / central registry for alternative data sources
# / adding a new alt-data source = one register() call, no core-file edits
# / each source declares how to fetch, store, and surface its value:
# /   - fetch: async(symbol, pool?) -> dict|None (or async(pool) -> dict for global sources)
# /   - store: async(pool, data, symbol?) -> None (or async(pool, symbol, data))
# /   - table: postgres table name (for endpoints + tests)
# /   - analysis_field: AnalysisData field name to populate
# /   - filter_config_key: base_strategy filter config key
# /   - cadence_seconds: how often the orchestrator refreshes this source
# / modeled on src/dashboard/indicator_registry.py

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import structlog

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class AltDataSource:
    # / immutable descriptor; value is what the orchestrator + analyst iterate over
    name: str
    fetch: Callable  # / async (symbol, pool=None) -> dict|None OR async (pool) -> dict
    store: Callable  # / async (pool, data) or (pool, symbol, data) -> None
    table: str
    analysis_field: str   # / AnalysisData field populated from the fetched value
    filter_config_key: str  # / key used in strategy config fundamental_filters
    cadence_seconds: int
    # / whether fetch signature is (pool) for global sources (macro) vs (symbol, pool=None) for per-symbol
    is_global: bool = False
    # / whether store signature is (pool, symbol, data) vs (pool, data)
    store_needs_symbol: bool = False
    # / whether to skip this source for etfs (some data — congressional, analyst, options — skip etfs)
    skip_etfs: bool = False


SOURCES: dict[str, AltDataSource] = {}


def register(source: AltDataSource) -> None:
    # / register an alt-data source. overwrites silently if name+filter_key already taken
    # / to support re-registration during tests
    key = f"{source.name}:{source.filter_config_key}"
    SOURCES[key] = source
    logger.debug("alt_data_source_registered", name=source.name, field=source.analysis_field)


def all_sources() -> list[AltDataSource]:
    # / ordered snapshot of registered sources for iteration
    return list(SOURCES.values())


def by_analysis_field(field: str) -> AltDataSource | None:
    # / lookup by AnalysisData field name (first match wins — some sources register twice
    # / for two fields, e.g. analyst_ratings → analyst_consensus + price_target_upside)
    for src in SOURCES.values():
        if src.analysis_field == field:
            return src
    return None


def by_filter_key(key: str) -> AltDataSource | None:
    # / lookup by strategy filter config key
    for src in SOURCES.values():
        if src.filter_config_key == key:
            return src
    return None


def by_name(name: str) -> list[AltDataSource]:
    # / all sources with a given name (analyst_ratings registers twice, for example)
    return [src for src in SOURCES.values() if src.name == name]


def clear() -> None:
    # / test helper — reset registry to empty state
    SOURCES.clear()


def _register_defaults() -> None:
    # / wire the live alt-data modules into the registry
    # / each entry points at the existing fetch/store functions; no new modules needed
    from src.data import (
        analyst_ratings,
        congressional_trades,
        dark_pool,
        earnings_revisions,
        fred_macro,
        options_data,
        short_interest,
    )

    # / fred macro — global (no symbol), populates AnalysisData.macro_score, filter macro_score_min
    register(AltDataSource(
        name="fred_macro",
        fetch=fred_macro.fetch_macro_indicators,  # / async(pool) -> dict
        store=_noop_store,  # / fetch_macro_indicators already persists via pool
        table="macro_data",
        analysis_field="macro_score",
        filter_config_key="macro_score_min",
        cadence_seconds=86400,
        is_global=True,
    ))

    # / congressional trades — per symbol, populates congressional_buy_ratio
    register(AltDataSource(
        name="congressional_trades",
        fetch=congressional_trades.fetch_congressional_trades,
        store=congressional_trades.store_congressional_trades,
        table="congressional_trades",
        analysis_field="congressional_buy_ratio",
        filter_config_key="congressional_buy_ratio_min",
        cadence_seconds=86400,
        skip_etfs=True,
    ))

    # / analyst ratings — one source, two AnalysisData fields
    register(AltDataSource(
        name="analyst_ratings",
        fetch=analyst_ratings.fetch_analyst_ratings,
        store=analyst_ratings.store_analyst_ratings,
        table="analyst_ratings",
        analysis_field="analyst_consensus",
        filter_config_key="analyst_consensus_min",
        cadence_seconds=86400,
        store_needs_symbol=True,
        skip_etfs=True,
    ))
    register(AltDataSource(
        name="analyst_ratings",
        fetch=analyst_ratings.fetch_analyst_ratings,
        store=analyst_ratings.store_analyst_ratings,
        table="analyst_ratings",
        analysis_field="price_target_upside",
        filter_config_key="price_target_upside_min",
        cadence_seconds=86400,
        store_needs_symbol=True,
        skip_etfs=True,
    ))

    # / earnings revisions
    register(AltDataSource(
        name="earnings_revisions",
        fetch=earnings_revisions.fetch_earnings_estimates,
        store=earnings_revisions.store_earnings_estimates,
        table="earnings_revisions",
        analysis_field="earnings_revision_momentum",
        filter_config_key="earnings_revision_momentum_min",
        cadence_seconds=86400,
        skip_etfs=True,
    ))

    # / short interest
    register(AltDataSource(
        name="short_interest",
        fetch=short_interest.fetch_short_interest,
        store=short_interest.store_short_interest,
        table="short_interest",
        analysis_field="short_pct_float",
        filter_config_key="short_pct_float_max",
        cadence_seconds=86400,
    ))

    # / dark pool — accepts (symbol, pool) so the ratio is computed in-flight
    register(AltDataSource(
        name="dark_pool",
        fetch=dark_pool.fetch_dark_pool_data,
        store=dark_pool.store_dark_pool,
        table="dark_pool",
        analysis_field="dark_pool_ratio",
        filter_config_key="dark_pool_ratio_max",
        cadence_seconds=86400,
    ))

    # / options data — two fields: iv_rank and put_call_ratio
    register(AltDataSource(
        name="options_data",
        fetch=options_data.fetch_options_data,
        store=options_data.store_options_data,
        table="options_data",
        analysis_field="iv_rank",
        filter_config_key="iv_rank_min",
        cadence_seconds=86400,
        skip_etfs=True,
    ))
    register(AltDataSource(
        name="options_data",
        fetch=options_data.fetch_options_data,
        store=options_data.store_options_data,
        table="options_data",
        analysis_field="put_call_ratio",
        filter_config_key="put_call_ratio_max",
        cadence_seconds=86400,
        skip_etfs=True,
    ))


async def _noop_store(*args, **kwargs) -> None:
    # / used for sources whose fetch function already persists (e.g. fred_macro)
    return None


# / register on import so callers can rely on `all_sources()` without explicit setup
_register_defaults()
