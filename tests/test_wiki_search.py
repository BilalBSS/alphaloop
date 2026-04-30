# / tests for WikiSearch — tsvector full-text search over wiki_documents

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.knowledge.wiki_search import WikiSearch


def _mock_pool(fetch_return=None):
    # / asyncpg pool -> async context manager -> connection mock pattern
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=fetch_return or [])
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=cm)
    return pool, conn


class TestSearchEmptyQuery:
    @pytest.mark.asyncio
    async def test_empty_string_returns_empty_no_db_call(self):
        pool, conn = _mock_pool()
        ws = WikiSearch(pool=pool)
        result = await ws.search("")
        assert result == []
        conn.fetch.assert_not_called()

    @pytest.mark.asyncio
    async def test_whitespace_only_returns_empty_no_db_call(self):
        pool, conn = _mock_pool()
        ws = WikiSearch(pool=pool)
        result = await ws.search("   \n\t  ")
        assert result == []
        conn.fetch.assert_not_called()


class TestSearchHappyPath:
    @pytest.mark.asyncio
    async def test_returns_rows_as_dicts(self):
        rows = [
            {"id": 1, "path": "regimes/bull.md", "category": "regimes",
             "title": "Bull", "symbols": [], "strategy_ids": [],
             "confidence": "emerging", "word_count": 100,
             "updated_at": None, "score": 0.5},
        ]
        pool, _ = _mock_pool(fetch_return=rows)
        ws = WikiSearch(pool=pool)
        result = await ws.search("bull market")
        assert len(result) == 1
        assert result[0]["path"] == "regimes/bull.md"
        assert result[0]["score"] == 0.5

    @pytest.mark.asyncio
    async def test_default_top_k_is_5(self):
        pool, conn = _mock_pool()
        ws = WikiSearch(pool=pool)
        await ws.search("query")
        args = conn.fetch.await_args.args
        # / last positional arg is top_k
        assert args[-1] == 5

    @pytest.mark.asyncio
    async def test_custom_top_k_forwarded(self):
        pool, conn = _mock_pool()
        ws = WikiSearch(pool=pool)
        await ws.search("query", top_k=25)
        assert conn.fetch.await_args.args[-1] == 25


class TestSearchFilters:
    @pytest.mark.asyncio
    async def test_category_filter_adds_clause(self):
        pool, conn = _mock_pool()
        ws = WikiSearch(pool=pool)
        await ws.search("query", category="regimes")
        sql = conn.fetch.await_args.args[0]
        assert "category = $" in sql
        # / query is $1, category is $2, top_k is $3
        args = conn.fetch.await_args.args
        assert args[1] == "query"
        assert args[2] == "regimes"
        assert args[3] == 5

    @pytest.mark.asyncio
    async def test_symbols_filter_adds_array_overlap(self):
        pool, conn = _mock_pool()
        ws = WikiSearch(pool=pool)
        await ws.search("query", symbols=["AAPL", "MSFT"])
        sql = conn.fetch.await_args.args[0]
        assert "symbols && $" in sql
        args = conn.fetch.await_args.args
        assert args[2] == ["AAPL", "MSFT"]

    @pytest.mark.asyncio
    async def test_both_filters_combined(self):
        pool, conn = _mock_pool()
        ws = WikiSearch(pool=pool)
        await ws.search("query", category="symbols", symbols=["AAPL"])
        sql = conn.fetch.await_args.args[0]
        assert "category = $" in sql
        assert "symbols && $" in sql
        args = conn.fetch.await_args.args
        # / order: query, category, symbols, top_k
        assert args[1] == "query"
        assert args[2] == "symbols"
        assert args[3] == ["AAPL"]
        assert args[4] == 5


class TestSearchSqlShape:
    @pytest.mark.asyncio
    async def test_uses_websearch_to_tsquery_and_rank_cd(self):
        pool, conn = _mock_pool()
        ws = WikiSearch(pool=pool)
        await ws.search("query")
        sql = conn.fetch.await_args.args[0]
        assert "websearch_to_tsquery('english'" in sql
        assert "ts_rank_cd(content_tsv" in sql
        assert "ORDER BY score DESC" in sql

    @pytest.mark.asyncio
    async def test_returns_list_of_plain_dicts(self):
        # / input rows look like asyncpg Records; output should be plain dicts
        rows = [{"path": "a.md", "score": 0.1}, {"path": "b.md", "score": 0.05}]
        pool, _ = _mock_pool(fetch_return=rows)
        ws = WikiSearch(pool=pool)
        result = await ws.search("q")
        assert isinstance(result, list)
        for r in result:
            assert isinstance(r, dict)


class TestSearchByCategory:
    @pytest.mark.asyncio
    async def test_with_query_delegates_to_search(self):
        rows = [{"id": 1, "path": "regimes/x.md", "category": "regimes",
                 "score": 0.9}]
        pool, conn = _mock_pool(fetch_return=rows)
        ws = WikiSearch(pool=pool)
        result = await ws.search_by_category(category="regimes", query="bull")
        # / delegates to search -> SQL uses websearch_to_tsquery
        assert len(result) == 1
        sql = conn.fetch.await_args.args[0]
        assert "websearch_to_tsquery" in sql

    @pytest.mark.asyncio
    async def test_without_query_lists_by_updated_at(self):
        pool, conn = _mock_pool()
        ws = WikiSearch(pool=pool)
        await ws.search_by_category(category="regimes")
        sql = conn.fetch.await_args.args[0]
        # / when no query, uses simple category filter, no tsvector
        assert "WHERE category = $1" in sql
        assert "ORDER BY updated_at DESC" in sql
        assert "websearch_to_tsquery" not in sql
        args = conn.fetch.await_args.args
        assert args[1] == "regimes"
        assert args[2] == 10  # / default top_k

    @pytest.mark.asyncio
    async def test_empty_string_query_treated_as_no_query(self):
        # / falsy empty-string query -> uses list-by-category path, not search
        pool, conn = _mock_pool()
        ws = WikiSearch(pool=pool)
        await ws.search_by_category(category="regimes", query="")
        sql = conn.fetch.await_args.args[0]
        assert "websearch_to_tsquery" not in sql

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_rows(self):
        pool, _ = _mock_pool(fetch_return=[])
        ws = WikiSearch(pool=pool)
        result = await ws.search_by_category(category="regimes")
        assert result == []


class TestCountByCategory:
    @pytest.mark.asyncio
    async def test_returns_dict_of_counts(self):
        rows = [
            {"category": "regimes", "n": 3},
            {"category": "symbols", "n": 10},
            {"category": "meta", "n": 1},
        ]
        pool, _ = _mock_pool(fetch_return=rows)
        ws = WikiSearch(pool=pool)
        result = await ws.count_by_category()
        assert result == {"regimes": 3, "symbols": 10, "meta": 1}

    @pytest.mark.asyncio
    async def test_empty_db_returns_empty_dict(self):
        pool, _ = _mock_pool(fetch_return=[])
        ws = WikiSearch(pool=pool)
        result = await ws.count_by_category()
        assert result == {}

    @pytest.mark.asyncio
    async def test_counts_cast_to_int(self):
        # / asyncpg may hand back bigint as int but cast guards against Decimal/str
        rows = [{"category": "regimes", "n": "7"}]
        pool, _ = _mock_pool(fetch_return=rows)
        ws = WikiSearch(pool=pool)
        result = await ws.count_by_category()
        assert result["regimes"] == 7
        assert isinstance(result["regimes"], int)


class TestDbErrorPropagation:
    @pytest.mark.asyncio
    async def test_db_exception_propagates(self):
        # / wiki_search is thin; caller decides error handling, so exception bubbles
        pool, conn = _mock_pool()
        conn.fetch = AsyncMock(side_effect=RuntimeError("db down"))
        ws = WikiSearch(pool=pool)
        with pytest.raises(RuntimeError, match="db down"):
            await ws.search("query")
