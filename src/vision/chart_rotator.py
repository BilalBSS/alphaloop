# / delete chart png files older than a cutoff + clean empty date dirs

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)


def _charts_root() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "charts"


def _parse_date_dir(name: str) -> date | None:
    # / date dir names are YYYY-MM-DD; anything else is ignored
    try:
        return datetime.strptime(name, "%Y-%m-%d").date()
    except ValueError:
        return None


async def rotate_old_charts(days: int = 30) -> int:
    # / delete pngs whose date dir is older than `days` and drop now-empty dirs
    root = _charts_root()
    if not root.exists():
        return 0
    cutoff = date.today() - timedelta(days=int(days))
    deleted_files = 0
    removed_dirs = 0

    for sub in sorted(root.iterdir()):
        try:
            if not sub.is_dir():
                continue
            dir_date = _parse_date_dir(sub.name)
            if dir_date is None or dir_date >= cutoff:
                continue
            for png in sub.glob("*.png"):
                try:
                    png.unlink()
                    deleted_files += 1
                except Exception as exc:
                    logger.warning(
                        "chart_rotate_unlink_failed",
                        path=str(png), error=str(exc)[:200],
                    )
            try:
                if not any(sub.iterdir()):
                    sub.rmdir()
                    removed_dirs += 1
            except Exception as exc:
                logger.debug("chart_rotate_rmdir_failed", path=str(sub), error=str(exc)[:120])
        except Exception as exc:
            logger.warning("chart_rotate_dir_failed", path=str(sub), error=str(exc)[:200])

    if deleted_files or removed_dirs:
        logger.info(
            "chart_rotation_complete",
            deleted_files=deleted_files, removed_dirs=removed_dirs, cutoff=str(cutoff),
        )
    return deleted_files
