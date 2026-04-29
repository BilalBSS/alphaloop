from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

router = APIRouter()

_VERSION_FILE = Path(__file__).parent.parent.parent.parent / "VERSION"


@router.get("/api/version")
async def get_version() -> dict[str, str]:
    return {"version": _VERSION_FILE.read_text().strip()}
