from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from fastapi import APIRouter

router = APIRouter()

_VERSION_FILE = Path(__file__).parent.parent.parent.parent / "VERSION"
_REPO_ROOT = Path(__file__).parent.parent.parent.parent
_DEPLOYED_AT = time.time()


def _resolve_git_sha() -> str:
    sha = os.environ.get("BUILD_SHA")
    if sha:
        return sha[:12]
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=_REPO_ROOT, capture_output=True, text=True, timeout=2.0, check=False,
        )
        if out.returncode == 0:
            return out.stdout.strip()[:12]
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown"


def _resolve_broker_mode() -> str:
    url = (os.environ.get("ALPACA_BASE_URL") or "").lower()
    if "paper" in url:
        return "paper"
    if url:
        return "live"
    return "paper"


_GIT_SHA = _resolve_git_sha()
_BROKER_MODE = _resolve_broker_mode()


@router.get("/api/version")
async def get_version() -> dict[str, str | float]:
    try:
        version = _VERSION_FILE.read_text().strip()
    except OSError:
        version = "unknown"
    return {
        "version": version,
        "git_sha": _GIT_SHA,
        "deployed_at": _DEPLOYED_AT,
        "broker_mode": _BROKER_MODE,
    }
