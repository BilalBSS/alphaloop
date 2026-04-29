
from __future__ import annotations

from datetime import date
from pathlib import Path

import aiofiles
import structlog

logger = structlog.get_logger(__name__)

CHANGELOG_PATH = Path(__file__).parent.parent.parent / "CHANGELOG.md"


async def update_docs(
    generation: int,
    report_path: str,
    changelog_path: Path | None = None,
) -> None:
    # / preserves existing content
    target = changelog_path or CHANGELOG_PATH
    today = date.today().isoformat()

    entry_lines = [
        "",
        f"## [Evolution Gen {generation}] - {today}",
        "",
        "### Evolution",
        f"- Generation {generation} evolution cycle completed",
        f"- Report: {report_path}",
        "",
    ]
    entry = "\n".join(entry_lines)

    if target.exists():
        async with aiofiles.open(target) as f:
            content = await f.read()

        lines = content.split("\n")
        insert_idx = None
        for i, line in enumerate(lines):
            if line.startswith("## "):
                insert_idx = i
                break

        if insert_idx is not None:
            new_content = "\n".join(lines[:insert_idx]) + entry + "\n".join(lines[insert_idx:])
        else:
            new_content = content + "\n" + entry
    else:
        new_content = "# Changelog\n\nAll notable changes to this project will be documented in this file.\n" + entry

    async with aiofiles.open(target, "w") as f:
        await f.write(new_content)

    logger.info("changelog_updated", generation=generation, path=str(target))
