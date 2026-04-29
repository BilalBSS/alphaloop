
from __future__ import annotations

import hmac

from fastapi import HTTPException, Request

from src.dashboard.state import STATE


def check_admin_token(supplied: str | None) -> bool:
    if not STATE.admin_token:
        return False
    if not supplied:
        return False
    return hmac.compare_digest(supplied.encode("utf-8"), STATE.admin_token.encode("utf-8"))


def extract_bearer(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip()
    return ""


async def require_admin_token(request: Request) -> None:
    if not STATE.admin_token:
        return
    supplied = extract_bearer(request)
    if not check_admin_token(supplied):
        raise HTTPException(status_code=401, detail="unauthorized")
