# / earnings transcripts: finnhub transcript fetch + storage
# / fetches transcript text for quarterly earnings calls

from __future__ import annotations

import os
from typing import Any

import structlog

from .resilience import api_get, with_retry

logger = structlog.get_logger(__name__)

FINNHUB_BASE = "https://finnhub.io/api/v1"


def _finnhub_headers() -> dict[str, str]:
    key = os.environ.get("FINNHUB_API_KEY", "")
    return {"X-Finnhub-Token": key}


@with_retry(source="finnhub", max_retries=2, base_delay=1.0)
async def _fetch_transcript_list(symbol: str) -> list[dict[str, Any]]:
    # / get list of available transcripts for symbol
    if not os.environ.get("FINNHUB_API_KEY"):
        return []
    url = f"{FINNHUB_BASE}/stock/transcripts/list"
    params = {"symbol": symbol}
    resp = await api_get(url, headers=_finnhub_headers(), params=params, source="finnhub")
    data = resp.json()
    return data.get("transcripts", [])


@with_retry(source="finnhub", max_retries=2, base_delay=1.0)
async def _fetch_transcript_by_id(transcript_id: str) -> str | None:
    if not os.environ.get("FINNHUB_API_KEY"):
        return None
    url = f"{FINNHUB_BASE}/stock/transcripts"
    params = {"id": transcript_id}
    resp = await api_get(url, headers=_finnhub_headers(), params=params, source="finnhub")
    data = resp.json()
    transcript_parts = data.get("transcript", [])
    if not transcript_parts:
        return None
    # / concatenate all speaker segments
    segments: list[str] = []
    for part in transcript_parts:
        name = part.get("name", "")
        speech = part.get("speech", [])
        text = " ".join(speech) if isinstance(speech, list) else str(speech)
        if name:
            segments.append(f"{name}: {text}")
        else:
            segments.append(text)
    return "\n\n".join(segments)


async def fetch_transcript(symbol: str) -> str | None:
    # / fetch most recent earnings transcript for symbol
    try:
        transcripts = await _fetch_transcript_list(symbol)
        if not transcripts:
            return None
        # / use the most recent transcript
        latest = transcripts[0]
        tid = latest.get("id")
        if not tid:
            return None
        text = await _fetch_transcript_by_id(tid)
        logger.info("transcript_fetched", symbol=symbol, length=len(text) if text else 0)
        return text
    except Exception as exc:
        logger.warning("transcript_fetch_failed", symbol=symbol, error=str(exc))
        return None


async def store_transcript(pool: Any, symbol: str, quarter: str, text: str | None) -> None:
    if not text:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO earnings_transcripts (symbol, quarter, transcript)
            VALUES ($1, $2, $3)
            ON CONFLICT (symbol, quarter) DO UPDATE SET
                transcript = EXCLUDED.transcript
            """,
            symbol, quarter, text,
        )
