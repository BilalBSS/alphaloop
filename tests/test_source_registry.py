# / tests for src.data.source_registry

from __future__ import annotations

import pytest

from src.data.source_registry import (
    AltDataSource,
    _register_defaults,
    all_sources,
    by_analysis_field,
    by_filter_key,
    by_name,
    clear,
    register,
)


def _restore_defaults():
    # / helper: restore default registrations after a test mutates SOURCES
    clear()
    _register_defaults()


class TestAltDataSourceDataclass:
    def test_frozen_dataclass(self):
        src = AltDataSource(
            name="t", fetch=lambda *a, **k: None, store=lambda *a, **k: None,
            table="t", analysis_field="f", filter_config_key="f_min",
            cadence_seconds=60,
        )
        with pytest.raises((AttributeError, Exception)):
            src.name = "new"  # type: ignore

    def test_defaults(self):
        src = AltDataSource(
            name="t", fetch=lambda *a, **k: None, store=lambda *a, **k: None,
            table="t", analysis_field="f", filter_config_key="f_min",
            cadence_seconds=60,
        )
        assert src.is_global is False
        assert src.store_needs_symbol is False
        assert src.skip_etfs is False


class TestRegister:
    def setup_method(self):
        _restore_defaults()

    def teardown_method(self):
        _restore_defaults()

    def test_register_adds_source(self):
        n = len(all_sources())
        src = AltDataSource(
            name="test_src", fetch=lambda *a, **k: None, store=lambda *a, **k: None,
            table="test_table", analysis_field="test_field",
            filter_config_key="test_field_min", cadence_seconds=60,
        )
        register(src)
        assert len(all_sources()) == n + 1
        assert any(s.filter_config_key == "test_field_min" for s in all_sources())

    def test_register_same_name_different_key_keeps_both(self):
        src1 = AltDataSource(
            name="dup", fetch=lambda *a, **k: None, store=lambda *a, **k: None,
            table="t", analysis_field="f1", filter_config_key="f1_min", cadence_seconds=60,
        )
        src2 = AltDataSource(
            name="dup", fetch=lambda *a, **k: None, store=lambda *a, **k: None,
            table="t", analysis_field="f2", filter_config_key="f2_min", cadence_seconds=60,
        )
        register(src1)
        register(src2)
        names = [s for s in all_sources() if s.name == "dup"]
        assert len(names) == 2

    def test_register_same_key_overwrites(self):
        src1 = AltDataSource(
            name="s", fetch=lambda *a, **k: None, store=lambda *a, **k: None,
            table="t", analysis_field="f", filter_config_key="f_min", cadence_seconds=60,
        )
        src2 = AltDataSource(
            name="s", fetch=lambda *a, **k: None, store=lambda *a, **k: None,
            table="t2", analysis_field="f", filter_config_key="f_min", cadence_seconds=120,
        )
        register(src1)
        count_before = len(by_name("s"))
        register(src2)
        count_after = len(by_name("s"))
        # / same name:filter_key overwrites
        assert count_after == count_before
        got = by_filter_key("f_min")
        assert got is not None and got.cadence_seconds == 120


class TestLookup:
    def setup_method(self):
        _restore_defaults()

    def teardown_method(self):
        _restore_defaults()

    def test_by_analysis_field_finds_source(self):
        src = by_analysis_field("macro_score")
        assert src is not None
        assert src.filter_config_key == "macro_score_min"

    def test_by_analysis_field_none_on_miss(self):
        assert by_analysis_field("does_not_exist") is None

    def test_by_filter_key_finds_source(self):
        src = by_filter_key("dark_pool_ratio_max")
        assert src is not None
        assert src.analysis_field == "dark_pool_ratio"

    def test_by_filter_key_none_on_miss(self):
        assert by_filter_key("fake_key") is None

    def test_by_name_returns_list(self):
        # / analyst_ratings is registered twice (two fields)
        items = by_name("analyst_ratings")
        assert len(items) == 2
        fields = {s.analysis_field for s in items}
        assert fields == {"analyst_consensus", "price_target_upside"}


class TestDefaultRegistrations:
    def setup_method(self):
        _restore_defaults()

    def teardown_method(self):
        _restore_defaults()

    def test_fred_macro_is_global(self):
        src = by_filter_key("macro_score_min")
        assert src is not None
        assert src.is_global is True
        assert src.name == "fred_macro"

    def test_fred_macro_table(self):
        src = by_filter_key("macro_score_min")
        assert src.table == "macro_data"

    def test_congressional_skips_etfs(self):
        src = by_filter_key("congressional_buy_ratio_min")
        assert src is not None
        assert src.skip_etfs is True

    def test_analyst_ratings_needs_symbol_store(self):
        src = by_filter_key("analyst_consensus_min")
        assert src is not None
        assert src.store_needs_symbol is True

    def test_price_target_upside_registered(self):
        src = by_filter_key("price_target_upside_min")
        assert src is not None
        assert src.analysis_field == "price_target_upside"

    def test_short_interest_registered(self):
        src = by_filter_key("short_pct_float_max")
        assert src is not None
        assert src.analysis_field == "short_pct_float"

    def test_dark_pool_registered(self):
        src = by_filter_key("dark_pool_ratio_max")
        assert src is not None
        assert src.analysis_field == "dark_pool_ratio"
        assert src.name == "dark_pool"

    def test_options_data_has_two_fields(self):
        iv = by_filter_key("iv_rank_min")
        pc = by_filter_key("put_call_ratio_max")
        assert iv is not None and iv.analysis_field == "iv_rank"
        assert pc is not None and pc.analysis_field == "put_call_ratio"

    def test_earnings_revisions_registered(self):
        src = by_filter_key("earnings_revision_momentum_min")
        assert src is not None
        assert src.analysis_field == "earnings_revision_momentum"

    def test_all_sources_nonempty(self):
        srcs = all_sources()
        assert len(srcs) >= 9  # / 7 distinct, analyst+options double-register

    def test_every_source_has_required_fields(self):
        for src in all_sources():
            assert src.name
            assert src.table
            assert src.analysis_field
            assert src.filter_config_key
            assert src.cadence_seconds > 0
            assert callable(src.fetch)
            assert callable(src.store)


class TestClear:
    def teardown_method(self):
        _restore_defaults()

    def test_clear_removes_all(self):
        assert len(all_sources()) > 0
        clear()
        assert len(all_sources()) == 0

    def test_register_defaults_repopulates(self):
        clear()
        _register_defaults()
        assert by_filter_key("macro_score_min") is not None


class TestFetchContract:
    # / smoke-test that registered sources have the expected signature shape
    # / (we don't actually call the fetch — that hits external APIs — but we
    # / verify the functions exist and are async-capable)
    def setup_method(self):
        _restore_defaults()

    def teardown_method(self):
        _restore_defaults()

    def test_fetch_is_coroutine_function(self):
        import inspect
        for src in all_sources():
            assert inspect.iscoroutinefunction(src.fetch), (
                f"{src.name}:{src.filter_config_key} fetch must be async"
            )

    def test_store_is_coroutine_function(self):
        import inspect
        for src in all_sources():
            assert inspect.iscoroutinefunction(src.store), (
                f"{src.name}:{src.filter_config_key} store must be async"
            )
