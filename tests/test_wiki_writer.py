# / tests for wiki_writer — focus on the path-escape SECURITY check + slugification
# / plus lock serialization + category validation + archive logic

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.knowledge.wiki_writer import (
    VALID_CATEGORIES,
    WikiWriter,
    _build_symbol_enrichment_prompt,
    _count_words,
    _slugify,
    _validate_category,
    enrich_symbol_doc,
    get_wiki_root,
    set_wiki_root,
)


def _mock_pool(mock_conn):
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_conn
    mock_ctx.__aexit__.return_value = False
    pool = MagicMock()
    pool.acquire.return_value = mock_ctx
    return pool


@pytest.fixture(autouse=True)
def _reset_wiki_root():
    # / prevent set_wiki_root leaks between tests (module-global state)
    import src.knowledge.wiki_writer as wwmod
    original = wwmod._WIKI_ROOT
    yield
    wwmod._WIKI_ROOT = original


class TestSlugify:
    def test_basic_lowercase(self):
        assert _slugify("Hello World") == "hello_world"

    def test_strips_special_chars(self):
        # / path-escape chars get stripped, not preserved
        assert ".." not in _slugify("../etc/passwd")
        assert "/" not in _slugify("foo/bar/baz")
        assert "\\" not in _slugify("foo\\bar")
        assert _slugify("file!@#$%^&*().md") == "filemd"

    def test_null_byte_stripped(self):
        # / null byte would terminate a path on some filesystems — must be stripped
        result = _slugify("evil\x00file")
        assert "\x00" not in result

    def test_length_capped(self):
        long_input = "a" * 200
        assert len(_slugify(long_input)) == 80

    def test_empty_returns_untitled(self):
        assert _slugify("") == "untitled"
        assert _slugify("!!!!!") == "untitled"

    def test_whitespace_collapsed(self):
        assert _slugify("many   spaces   here") == "many_spaces_here"


class TestValidateCategory:
    def test_valid_categories(self):
        for cat in VALID_CATEGORIES:
            _validate_category(cat)  # / should not raise

    def test_invalid_category_raises(self):
        with pytest.raises(ValueError, match="invalid wiki category"):
            _validate_category("bogus")

    def test_case_sensitive(self):
        with pytest.raises(ValueError):
            _validate_category("Regimes")  # / capital R rejected


class TestCountWords:
    def test_basic_count(self):
        assert _count_words("hello world") == 2

    def test_whitespace_only_zero(self):
        assert _count_words("   \n\t  ") == 0

    def test_empty_zero(self):
        assert _count_words("") == 0

    def test_collapses_whitespace(self):
        assert _count_words("one  two\t\tthree\nfour") == 4


class TestWikiRoot:
    def test_default_root_resolves_to_trading_wiki(self):
        # / when _WIKI_ROOT is None, get_wiki_root() defaults to <project>/trading-wiki
        import src.knowledge.wiki_writer as wwmod
        wwmod._WIKI_ROOT = None
        root = get_wiki_root()
        assert root.name == "trading-wiki"

    def test_set_wiki_root_overrides(self, tmp_path):
        set_wiki_root(tmp_path)
        assert get_wiki_root() == tmp_path


class TestWriteDocumentPathEscape:
    # / SECURITY: the path-escape check MUST reject filenames that try to break out
    # / of the wiki root via ../, absolute paths, backslash, etc.

    @pytest.mark.asyncio
    async def test_dotdot_escape_rejected(self, tmp_path):
        writer = WikiWriter(pool=None, root=tmp_path)
        # / slugify will strip the ../ but we still verify the resolved path stays under root
        result = await writer.write_document(
            "regimes", "../../etc/passwd", content="hostile",
        )
        # / result path is the slugified relative path under the category dir
        assert result.startswith("regimes/")
        assert ".." not in result
        # / file actually lives under tmp_path/regimes/
        written = tmp_path / result
        assert written.exists()
        assert written.read_text() == "hostile"
        # / confirm nothing escaped the root
        for p in tmp_path.rglob("*"):
            if p.is_file():
                assert str(p.resolve()).startswith(str(tmp_path.resolve()))

    @pytest.mark.asyncio
    async def test_absolute_path_slugified_harmless(self, tmp_path):
        writer = WikiWriter(pool=None, root=tmp_path)
        result = await writer.write_document(
            "regimes", "/etc/shadow", content="x",
        )
        # / slugified — leading slash gone, result under regimes/
        assert result.startswith("regimes/")
        assert not Path(result).is_absolute()

    @pytest.mark.asyncio
    async def test_null_byte_in_filename_slugified(self, tmp_path):
        writer = WikiWriter(pool=None, root=tmp_path)
        # / null byte is non-word/whitespace — slugify's regex strips it
        result = await writer.write_document(
            "regimes", "safe\x00file", content="x",
        )
        assert "\x00" not in result

    @pytest.mark.asyncio
    async def test_md_extension_stripped_before_slugify(self, tmp_path):
        writer = WikiWriter(pool=None, root=tmp_path)
        result = await writer.write_document(
            "regimes", "my_doc.md", content="x",
        )
        # / .md stripped before slugify, then reappended — no double extension
        assert result == "regimes/my_doc.md"

    @pytest.mark.asyncio
    async def test_invalid_category_raises(self, tmp_path):
        writer = WikiWriter(pool=None, root=tmp_path)
        with pytest.raises(ValueError, match="invalid wiki category"):
            await writer.write_document("bogus", "foo", content="x")


class TestWriteDocumentRegister:
    @pytest.mark.asyncio
    async def test_writes_file_and_registers_when_pool_provided(self, tmp_path):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {"id": 42}
        pool = _mock_pool(mock_conn)
        writer = WikiWriter(pool=pool, root=tmp_path)
        await writer.write_document(
            "regimes", "bull_market",
            content="# Bull Market\n\nBody here.",
            title="Bull Market",
            symbols=["SPY", "QQQ"],
            strategy_ids=["strategy_001"],
        )
        # / file exists on disk
        assert (tmp_path / "regimes" / "bull_market.md").exists()
        # / db register was called once
        assert mock_conn.fetchrow.call_count == 1
        # / the INSERT SQL ran with the expected args
        args = mock_conn.fetchrow.call_args[0]
        assert "INSERT INTO wiki_documents" in args[0]
        assert args[1] == "regimes/bull_market.md"  # / rel path
        assert args[4] == ["SPY", "QQQ"]  # / symbols
        assert args[5] == ["strategy_001"]  # / strategy_ids

    @pytest.mark.asyncio
    async def test_no_pool_skips_register(self, tmp_path):
        writer = WikiWriter(pool=None, root=tmp_path)
        result = await writer.write_document("regimes", "test", content="x")
        assert (tmp_path / result).exists()


class TestAppendSection:
    @pytest.mark.asyncio
    async def test_append_to_existing_doc(self, tmp_path):
        writer = WikiWriter(pool=None, root=tmp_path)
        await writer.write_document("regimes", "bull", content="# Bull\n\nBody.")
        await writer.append_section("regimes/bull.md", "Update", "New findings.")
        contents = (tmp_path / "regimes" / "bull.md").read_text()
        assert "# Bull" in contents
        assert "## Update" in contents
        assert "New findings." in contents

    @pytest.mark.asyncio
    async def test_append_missing_doc_raises(self, tmp_path):
        writer = WikiWriter(pool=None, root=tmp_path)
        with pytest.raises(FileNotFoundError):
            await writer.append_section("regimes/missing.md", "Update", "body")


class TestReadDocument:
    @pytest.mark.asyncio
    async def test_read_existing(self, tmp_path):
        writer = WikiWriter(pool=None, root=tmp_path)
        await writer.write_document("regimes", "bull", content="hello world")
        content = await writer.read_document("regimes/bull.md")
        assert content == "hello world"

    @pytest.mark.asyncio
    async def test_read_missing_returns_none(self, tmp_path):
        writer = WikiWriter(pool=None, root=tmp_path)
        assert await writer.read_document("regimes/missing.md") is None


class TestListDocuments:
    @pytest.mark.asyncio
    async def test_no_pool_returns_empty(self, tmp_path):
        writer = WikiWriter(pool=None, root=tmp_path)
        assert await writer.list_documents() == []

    @pytest.mark.asyncio
    async def test_filters_build_correct_sql(self, tmp_path):
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        pool = _mock_pool(mock_conn)
        writer = WikiWriter(pool=pool, root=tmp_path)
        await writer.list_documents(
            category="regimes", symbols=["SPY"], strategy_ids=["s1"], limit=10,
        )
        sql = mock_conn.fetch.call_args[0][0]
        assert "category = $1" in sql
        assert "symbols && $2" in sql
        assert "strategy_ids && $3" in sql
        assert "LIMIT $4" in sql

    @pytest.mark.asyncio
    async def test_invalid_category_in_list_raises(self, tmp_path):
        mock_conn = AsyncMock()
        pool = _mock_pool(mock_conn)
        writer = WikiWriter(pool=pool, root=tmp_path)
        with pytest.raises(ValueError):
            await writer.list_documents(category="bogus")


class TestArchiveOld:
    @pytest.mark.asyncio
    async def test_no_pool_returns_zero(self, tmp_path):
        writer = WikiWriter(pool=None, root=tmp_path)
        assert await writer.archive_old() == 0

    @pytest.mark.asyncio
    async def test_archives_matching_docs(self, tmp_path):
        # / create two docs; mock returns one as "old"
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [
            {"id": 1, "path": "regimes/stale.md"},
        ]
        pool = _mock_pool(mock_conn)
        writer = WikiWriter(pool=pool, root=tmp_path)
        (tmp_path / "regimes").mkdir(parents=True)
        (tmp_path / "regimes" / "stale.md").write_text("old content")
        moved = await writer.archive_old(older_than_days=30)
        assert moved == 1
        assert (tmp_path / "archive" / "stale.md").exists()
        assert not (tmp_path / "regimes" / "stale.md").exists()

    @pytest.mark.asyncio
    async def test_missing_file_skipped(self, tmp_path):
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [
            {"id": 1, "path": "regimes/gone.md"},
        ]
        pool = _mock_pool(mock_conn)
        writer = WikiWriter(pool=pool, root=tmp_path)
        # / file doesn't exist on disk — archive_old should skip, not crash
        moved = await writer.archive_old(older_than_days=30)
        assert moved == 0


class TestLockSerialization:
    @pytest.mark.asyncio
    async def test_concurrent_writes_to_same_path_serialize(self, tmp_path):
        # / two coroutines writing to the same rel_path should not interleave
        # / the final file content must be ONE of the full writes, not a mix
        writer = WikiWriter(pool=None, root=tmp_path)

        async def write_a():
            await writer.write_document("regimes", "race", content="A" * 100)

        async def write_b():
            await writer.write_document("regimes", "race", content="B" * 100)

        await asyncio.gather(write_a(), write_b())
        content = (tmp_path / "regimes" / "race.md").read_text()
        # / must be all A's or all B's — never interleaved
        assert content == "A" * 100 or content == "B" * 100


# ──────────────────────────────────────────────────────
# / phase 5 step 3: enrich_symbol_doc
# ──────────────────────────────────────────────────────

class TestBuildSymbolEnrichmentPrompt:
    def test_includes_symbol_and_sections(self):
        prompt = _build_symbol_enrichment_prompt(
            "AAPL",
            analysis_history=[
                {"date": "2026-01-01", "fundamental_score": 75,
                 "technical_score": 60, "composite_score": 68, "regime": "bull"},
            ],
            fundamentals={"pe_ratio": 28.5, "fcf_margin": 0.25, "sector": "mega_tech"},
            insider_trades=[
                {"filing_date": "2025-12-15", "insider_name": "Tim Cook",
                 "transaction_type": "buy", "shares": 1000, "total_value": 175000},
            ],
        )
        assert "AAPL" in prompt
        assert "Analysis History" in prompt
        assert "Fundamentals" in prompt
        assert "Insider Activity" in prompt
        assert "Task" in prompt

    def test_missing_data_still_produces_prompt(self):
        # / all optional data missing — still returns a usable task prompt
        prompt = _build_symbol_enrichment_prompt("TSLA", [], None, None)
        assert "TSLA" in prompt
        assert "Task" in prompt

    def test_insider_buy_sell_counts(self):
        prompt = _build_symbol_enrichment_prompt(
            "X", [], None,
            [
                {"transaction_type": "buy", "total_value": 1},
                {"transaction_type": "buy", "total_value": 2},
                {"transaction_type": "sell", "total_value": 3},
            ],
        )
        assert "buys: 2" in prompt
        assert "sells: 1" in prompt

    def test_analysis_history_truncated_to_10(self):
        # / should include at most 10 most recent rows
        history = [
            {"date": f"2026-01-{i:02d}", "composite_score": i,
             "fundamental_score": None, "technical_score": None, "regime": "bull"}
            for i in range(1, 21)
        ]
        prompt = _build_symbol_enrichment_prompt("X", history, None, None)
        # / 10 rows visible
        assert "composite=1 " in prompt
        assert "composite=10 " in prompt
        # / 11th should NOT be in the prompt
        assert "composite=11 " not in prompt


class TestEnrichSymbolDoc:
    @pytest.mark.asyncio
    async def test_empty_symbol_short_circuits(self, tmp_path):
        result = await enrich_symbol_doc(None, "", [], None, [])
        assert result == (None, None)

    @pytest.mark.asyncio
    async def test_no_groq_key_skips_llm(self, tmp_path, monkeypatch):
        # / without GROQ_API_KEY the llm chain bails out and we return (None, None)
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        result = await enrich_symbol_doc(None, "AAPL", [], None, [])
        assert result == (None, None)

    @pytest.mark.asyncio
    async def test_happy_path_writes_markdown_and_returns_content(self, tmp_path, monkeypatch):
        # / when the llm succeeds, we write a file + return doc_id + content
        monkeypatch.setenv("GROQ_API_KEY", "fake-key-for-test")
        set_wiki_root(tmp_path)

        mock_conn = AsyncMock()
        # / fetchrow covers both the write_document INSERT RETURNING id and the post-write id lookup
        mock_conn.fetchrow.side_effect = [
            {"id": 99},   # / register insert
            {"id": 99},   # / post-write lookup
        ]
        pool = _mock_pool(mock_conn)

        async def _fake_generate(prompt, symbol):
            return (f"## Overview\n\n{symbol} is a leading mega-cap name.\n\n"
                    "## Playbook\n\n- rule 1\n- rule 2", "llama-3.3-70b-versatile")

        from unittest.mock import patch as _patch
        with _patch(
            "src.knowledge.wiki_writer._generate_symbol_enrichment",
            side_effect=_fake_generate,
        ):
            doc_id, content = await enrich_symbol_doc(
                pool, "AAPL", [{"date": "2026-01-01", "composite_score": 70}],
                {"pe_ratio": 28.5}, [],
            )

        assert doc_id == 99
        assert content is not None
        assert "AAPL Playbook" in content
        # / file actually exists
        assert (tmp_path / "symbols" / "aapl.md").exists()

    @pytest.mark.asyncio
    async def test_llm_returns_none_no_write(self, tmp_path, monkeypatch):
        # / if every provider in the chain fails, we skip the write
        monkeypatch.setenv("GROQ_API_KEY", "fake-key")
        set_wiki_root(tmp_path)

        mock_conn = AsyncMock()
        pool = _mock_pool(mock_conn)

        from unittest.mock import patch as _patch
        with _patch(
            "src.knowledge.wiki_writer._generate_symbol_enrichment",
            return_value=(None, None),
        ):
            doc_id, content = await enrich_symbol_doc(
                pool, "AAPL", [], None, [],
            )

        assert doc_id is None
        assert content is None
        # / nothing was written to disk
        assert not (tmp_path / "symbols" / "aapl.md").exists()
