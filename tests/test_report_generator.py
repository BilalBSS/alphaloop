# / tests for report_generator

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from src.evolution.report_generator import generate_report


def _pool_summary(total: int = 10, active: int = 5) -> dict:
    return {
        "total": total,
        "active": active,
        "by_status": {"paper_trading": 3, "live": 2, "killed": 5},
        "top_3": [
            {"id": "s9", "score": 1.234},
            {"id": "s8", "score": 0.987},
            {"id": "s7", "score": 0.654},
        ],
    }


# ────────────────────────────────────────────────────────────────
# report content
# ────────────────────────────────────────────────────────────────


class TestReportContent:
    @pytest.mark.asyncio
    async def test_report_contains_generation(self, tmp_path: Path):
        with patch("src.evolution.report_generator.REPORTS_DIR", tmp_path):
            report = await generate_report(
                generation=7,
                killed=[{"id": "s0", "reason": "bottom quartile"}],
                mutated=[],
                promoted=[],
                pool_summary=_pool_summary(),
            )

        assert "Generation 7" in report

    @pytest.mark.asyncio
    async def test_report_lists_killed(self, tmp_path: Path):
        with patch("src.evolution.report_generator.REPORTS_DIR", tmp_path):
            report = await generate_report(
                generation=1,
                killed=[
                    {"id": "s0", "reason": "bottom quartile (composite=0.12)"},
                    {"id": "s1", "reason": "bottom quartile (composite=0.08)"},
                ],
                mutated=[],
                promoted=[],
                pool_summary=_pool_summary(),
            )

        assert "s0" in report
        assert "s1" in report
        assert "bottom quartile" in report

    @pytest.mark.asyncio
    async def test_report_lists_mutated(self, tmp_path: Path):
        with patch("src.evolution.report_generator.REPORTS_DIR", tmp_path):
            report = await generate_report(
                generation=1,
                killed=[],
                mutated=[{"id": "m1", "parent_id": "s0", "status": "paper_trading"}],
                promoted=[],
                pool_summary=_pool_summary(),
            )

        assert "m1" in report
        assert "s0" in report
        assert "paper_trading" in report

    @pytest.mark.asyncio
    async def test_report_lists_promoted(self, tmp_path: Path):
        with patch("src.evolution.report_generator.REPORTS_DIR", tmp_path):
            report = await generate_report(
                generation=1,
                killed=[],
                mutated=[],
                promoted=[{"id": "p1"}],
                pool_summary=_pool_summary(),
            )

        assert "p1" in report
        assert "promoted to live" in report

    @pytest.mark.asyncio
    async def test_empty_sections(self, tmp_path: Path):
        with patch("src.evolution.report_generator.REPORTS_DIR", tmp_path):
            report = await generate_report(
                generation=1,
                killed=[],
                mutated=[],
                promoted=[],
                pool_summary=_pool_summary(),
            )

        # / empty sections show "None"
        lines = report.split("\n")
        # / find the "Killed Strategies" section and check "None" follows
        for i, line in enumerate(lines):
            if "Killed Strategies" in line:
                # / skip blank line, check for "None"
                remaining = "\n".join(lines[i:i+4])
                assert "None" in remaining
                break

    @pytest.mark.asyncio
    async def test_pool_summary_included(self, tmp_path: Path):
        summary = _pool_summary(total=15, active=8)
        with patch("src.evolution.report_generator.REPORTS_DIR", tmp_path):
            report = await generate_report(
                generation=1,
                killed=[],
                mutated=[],
                promoted=[],
                pool_summary=summary,
            )

        assert "15" in report
        assert "8" in report
        assert "Pool Summary" in report

    @pytest.mark.asyncio
    async def test_top_performers_in_report(self, tmp_path: Path):
        with patch("src.evolution.report_generator.REPORTS_DIR", tmp_path):
            report = await generate_report(
                generation=1,
                killed=[],
                mutated=[],
                promoted=[],
                pool_summary=_pool_summary(),
            )

        assert "s9" in report
        assert "1.2340" in report
        assert "Top Performers" in report


# ────────────────────────────────────────────────────────────────
# file creation
# ────────────────────────────────────────────────────────────────


class TestFileCreation:
    @pytest.mark.asyncio
    async def test_report_file_created(self, tmp_path: Path):
        with patch("src.evolution.report_generator.REPORTS_DIR", tmp_path):
            await generate_report(
                generation=42,
                killed=[],
                mutated=[],
                promoted=[],
                pool_summary=_pool_summary(),
            )

        expected_path = tmp_path / "evolution_gen_42.md"
        assert expected_path.exists()
        content = expected_path.read_text()
        assert "Generation 42" in content

    @pytest.mark.asyncio
    async def test_report_creates_dir_if_missing(self, tmp_path: Path):
        reports_dir = tmp_path / "nested" / "reports"
        with patch("src.evolution.report_generator.REPORTS_DIR", reports_dir):
            await generate_report(
                generation=1,
                killed=[],
                mutated=[],
                promoted=[],
                pool_summary=_pool_summary(),
            )

        assert (reports_dir / "evolution_gen_1.md").exists()

    @pytest.mark.asyncio
    async def test_report_date_present(self, tmp_path: Path):
        from datetime import date
        today = date.today().isoformat()

        with patch("src.evolution.report_generator.REPORTS_DIR", tmp_path):
            report = await generate_report(
                generation=1,
                killed=[],
                mutated=[],
                promoted=[],
                pool_summary=_pool_summary(),
            )

        assert today in report
