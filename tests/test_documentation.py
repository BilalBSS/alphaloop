# / tests for documentation.py

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from src.evolution.documentation import update_docs

SAMPLE_CHANGELOG = """# Changelog

All notable changes to this project will be documented in this file.

## [0.6.1.0] - 2026-03-26

### Changed
- test hardening across all 32 test files

## [0.6.0.0] - 2026-03-26

### Added
- Quant engine (Phase 6): 6 modules in src/quant/
"""


# ────────────────────────────────────────────────────────────────
# insertion position
# ────────────────────────────────────────────────────────────────


class TestInsertPosition:
    @pytest.mark.asyncio
    async def test_inserts_entry_at_correct_position(self, tmp_path: Path):
        # / entry should appear before the first ## header
        changelog = tmp_path / "CHANGELOG.md"
        changelog.write_text(SAMPLE_CHANGELOG)

        await update_docs(7, "reports/evolution_gen_7.md", changelog_path=changelog)

        content = changelog.read_text()
        # / the evolution entry should come before [0.6.1.0]
        evo_idx = content.index("Evolution Gen 7")
        old_idx = content.index("[0.6.1.0]")
        assert evo_idx < old_idx

    @pytest.mark.asyncio
    async def test_preserves_existing_content(self, tmp_path: Path):
        changelog = tmp_path / "CHANGELOG.md"
        changelog.write_text(SAMPLE_CHANGELOG)

        await update_docs(3, "reports/evolution_gen_3.md", changelog_path=changelog)

        content = changelog.read_text()
        # / original content still present
        assert "[0.6.1.0]" in content
        assert "[0.6.0.0]" in content
        assert "test hardening" in content
        assert "Quant engine" in content

    @pytest.mark.asyncio
    async def test_correct_date_format(self, tmp_path: Path):
        changelog = tmp_path / "CHANGELOG.md"
        changelog.write_text(SAMPLE_CHANGELOG)

        await update_docs(5, "reports/evolution_gen_5.md", changelog_path=changelog)

        content = changelog.read_text()
        today = date.today().isoformat()  # / YYYY-MM-DD
        assert today in content

    @pytest.mark.asyncio
    async def test_references_report_path(self, tmp_path: Path):
        changelog = tmp_path / "CHANGELOG.md"
        changelog.write_text(SAMPLE_CHANGELOG)

        await update_docs(9, "reports/evolution_gen_9.md", changelog_path=changelog)

        content = changelog.read_text()
        assert "reports/evolution_gen_9.md" in content


# ────────────────────────────────────────────────────────────────
# edge cases
# ────────────────────────────────────────────────────────────────


class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_handles_empty_changelog(self, tmp_path: Path):
        # / changelog exists but empty
        changelog = tmp_path / "CHANGELOG.md"
        changelog.write_text("")

        await update_docs(1, "reports/evolution_gen_1.md", changelog_path=changelog)

        content = changelog.read_text()
        assert "Evolution Gen 1" in content
        assert "Generation 1 evolution cycle completed" in content

    @pytest.mark.asyncio
    async def test_handles_missing_changelog(self, tmp_path: Path):
        # / changelog doesn't exist at all
        changelog = tmp_path / "CHANGELOG.md"
        assert not changelog.exists()

        await update_docs(1, "reports/evolution_gen_1.md", changelog_path=changelog)

        assert changelog.exists()
        content = changelog.read_text()
        assert "# Changelog" in content
        assert "Evolution Gen 1" in content

    @pytest.mark.asyncio
    async def test_changelog_without_headers(self, tmp_path: Path):
        # / changelog with no ## headers
        changelog = tmp_path / "CHANGELOG.md"
        changelog.write_text("# Changelog\n\nJust some text.\n")

        await update_docs(2, "reports/evolution_gen_2.md", changelog_path=changelog)

        content = changelog.read_text()
        assert "Evolution Gen 2" in content
        assert "Just some text" in content

    @pytest.mark.asyncio
    async def test_multiple_updates_stack(self, tmp_path: Path):
        # / two consecutive updates both appear
        changelog = tmp_path / "CHANGELOG.md"
        changelog.write_text(SAMPLE_CHANGELOG)

        await update_docs(10, "reports/evolution_gen_10.md", changelog_path=changelog)
        await update_docs(11, "reports/evolution_gen_11.md", changelog_path=changelog)

        content = changelog.read_text()
        assert "Evolution Gen 10" in content
        assert "Evolution Gen 11" in content
        # / gen 11 should come before gen 10 (newer on top)
        idx_11 = content.index("Evolution Gen 11")
        idx_10 = content.index("Evolution Gen 10")
        assert idx_11 < idx_10
