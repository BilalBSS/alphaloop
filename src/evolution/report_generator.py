# / writes to reports/evolution_gen_{N}.md

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import aiofiles
import structlog

logger = structlog.get_logger(__name__)

REPORTS_DIR = Path(__file__).parent.parent.parent / "reports"


async def generate_report(
    generation: int,
    killed: list[dict[str, Any]],
    mutated: list[dict[str, Any]],
    promoted: list[dict[str, Any]],
    pool_summary: dict[str, Any],
) -> str:
    # / returns the report string
    today = date.today().isoformat()
    report_lines = [
        f"# Evolution Report - Generation {generation}",
        "",
        f"**Date**: {today}",
        "",
        "## Pool Summary",
        "",
        f"- Total strategies: {pool_summary.get('total', 0)}",
        f"- Active (paper + live): {pool_summary.get('active', 0)}",
    ]

    by_status = pool_summary.get("by_status", {})
    if by_status:
        report_lines.append(f"- Status breakdown: {', '.join(f'{k}={v}' for k, v in sorted(by_status.items()))}")
    else:
        report_lines.append("- Status breakdown: empty")

    top_3 = pool_summary.get("top_3", [])
    if top_3:
        report_lines.append("")
        report_lines.append("### Top Performers")
        report_lines.append("")
        for entry in top_3:
            score_str = f"{entry['score']:.4f}" if entry.get("score") is not None else "unscored"
            report_lines.append(f"- {entry['id']}: {score_str}")

    report_lines.append("")
    report_lines.append("## Killed Strategies")
    report_lines.append("")
    if killed:
        for k in killed:
            sid = k.get("id", "unknown")
            reason = k.get("reason", "bottom quartile")
            report_lines.append(f"- {sid}: {reason}")
    else:
        report_lines.append("None")

    report_lines.append("")
    report_lines.append("## Mutated Strategies")
    report_lines.append("")
    if mutated:
        for m in mutated:
            new_id = m.get("id", "unknown")
            parent_id = m.get("parent_id", "unknown")
            status = m.get("status", "unknown")
            report_lines.append(f"- {new_id} (from {parent_id}): {status}")
    else:
        report_lines.append("None")

    report_lines.append("")
    report_lines.append("## Promoted Strategies")
    report_lines.append("")
    if promoted:
        for p in promoted:
            sid = p.get("id", "unknown")
            report_lines.append(f"- {sid}: promoted to live")
    else:
        report_lines.append("None")

    report_lines.append("")
    report = "\n".join(report_lines) + "\n"

    # / write to file
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / f"evolution_gen_{generation}.md"

    async with aiofiles.open(report_path, "w") as f:
        await f.write(report)

    logger.info("evolution_report_written", generation=generation, path=str(report_path))
    return report
