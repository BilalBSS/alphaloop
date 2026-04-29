
from __future__ import annotations

import json
from typing import Any

import structlog

from src.knowledge.wiki_writer import WikiWriter

logger = structlog.get_logger(__name__)

VALID_LESSON_TYPES = {
    "mutation_result", "killed", "promotion", "decay_detected",
    "regime_performance", "dormant_detected", "activation",
}


def _infer_confidence(trade_count: int | None) -> str:
    if trade_count is None:
        return "anecdotal"
    if trade_count < 10:
        return "anecdotal"
    if trade_count < 30:
        return "emerging"
    if trade_count < 100:
        return "established"
    return "canonical"


class StrategyLessons:
    def __init__(self, pool, writer: WikiWriter | None = None):
        self._pool = pool
        self._writer = writer or WikiWriter(pool=pool)

    async def record(
        self,
        strategy_id: str,
        lesson_type: str,
        content: str,
        context: dict[str, Any] | None = None,
        trade_count: int | None = None,
    ) -> int:
        if lesson_type not in VALID_LESSON_TYPES:
            raise ValueError(f"invalid lesson_type: {lesson_type!r}")
        confidence = _infer_confidence(trade_count)
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO strategy_lessons
                    (strategy_id, lesson_type, content, context, confidence, trade_count)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING id
                """,
                strategy_id, lesson_type, content,
                json.dumps(context) if context else None,
                confidence, trade_count,
            )
        lesson_id = int(row["id"])
        await self._append_to_playbook(strategy_id, lesson_type, content, confidence)
        logger.info(
            "strategy_lesson_recorded",
            strategy_id=strategy_id, lesson_type=lesson_type, confidence=confidence,
        )
        return lesson_id

    async def get_context(self, strategy_id: str, limit: int = 10) -> list[dict]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT lesson_type, content, confidence, trade_count, created_at
                FROM strategy_lessons
                WHERE strategy_id = $1
                ORDER BY created_at DESC
                LIMIT $2
                """,
                strategy_id, limit,
            )
        return [dict(r) for r in rows]

    async def _append_to_playbook(
        self, strategy_id: str, lesson_type: str, content: str, confidence: str,
    ) -> None:
        rel_path = f"strategies/{strategy_id}.md"
        existing = await self._writer.read_document(rel_path)
        if existing is None:
            stub = (
                f"# {strategy_id} Playbook\n\n"
                f"*auto-generated stub — seed script should populate this*\n\n"
                f"## lessons\n"
            )
            await self._writer.write_document(
                category="strategies", filename=f"{strategy_id}.md",
                content=stub, title=f"{strategy_id} playbook",
                strategy_ids=[strategy_id],
            )
        await self._writer.append_section(
            rel_path=rel_path,
            heading=f"lesson: {lesson_type} [{confidence}]",
            body=content,
        )
