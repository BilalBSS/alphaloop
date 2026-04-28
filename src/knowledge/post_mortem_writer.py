# / post-mortem writer — triggered on trade close with loss over threshold
# / narrative from groq/cerebras chain, template fallback on total failure

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

import structlog

from src.analysis.ai_summary import (
    CEREBRAS_FAST_MODEL, CEREBRAS_MODEL,
    DEFAULT_MODEL, FALLBACK_MODEL,
    _RateLimited, _call_cerebras, _call_llm,
)
from src.knowledge.db_helpers import (
    claim_post_mortem_slot,
    set_post_mortem_wiki_path,
    update_post_mortem_details,
)
from src.knowledge.wiki_writer import WikiWriter

logger = structlog.get_logger(__name__)

_POST_MORTEM_SYSTEM_MSG = (
    "You are a senior trading post-mortem analyst. A trade just closed at a loss. "
    "Given the trade context, strategy config, and recent history, produce a concise "
    "diagnostic of what likely went wrong and one concrete change the strategy could try.\n"
    "Rules:\n"
    "- Keep it under 180 words\n"
    "- Lead with the single most likely failure mode\n"
    "- Do not speculate beyond the data provided\n"
    "- End with a one-sentence prescription for future cycles"
)


def _build_prompt(
    symbol: str,
    strategy_id: str,
    pnl: float,
    trigger_type: str,
    deviation_sigma: float | None,
    trade: dict[str, Any] | None,
    strategy_config: dict[str, Any] | None,
    recent_trades: list[dict[str, Any]] | None,
) -> str:
    # / compose the prompt from serialized context
    parts: list[str] = [
        f"Trade closed at a loss for strategy {strategy_id} on {symbol}.",
        f"Realized PnL: {pnl:.2f}",
        f"Trigger: {trigger_type}",
    ]
    if deviation_sigma is not None:
        parts.append(f"Deviation (sigma vs expected): {deviation_sigma:.2f}")

    if trade:
        parts.append("\n## Closing Trade")
        for key in ("side", "qty", "price", "order_id", "broker", "regime"):
            if trade.get(key) is not None:
                parts.append(f"  {key}: {trade[key]}")
        if trade.get("details"):
            parts.append(f"  details: {json.dumps(trade['details'], default=str)[:400]}")

    if strategy_config:
        parts.append("\n## Strategy Config")
        # / surface the moving pieces the LLM can reason about
        for key in (
            "name", "asset_class", "universe", "tier", "sector", "symbol",
            "fundamental_filters", "entry_conditions", "exit_conditions",
            "position_sizing",
        ):
            val = strategy_config.get(key)
            if val is not None:
                parts.append(f"  {key}: {json.dumps(val, default=str)[:400]}")

    if recent_trades:
        parts.append("\n## Recent Trades (same strategy)")
        for t in recent_trades[:8]:
            parts.append(
                "  - "
                + f"{t.get('symbol', '?')} {t.get('side', '?')} "
                + f"pnl={t.get('pnl')} at={t.get('created_at')}"
            )

    parts.append("\n## Task")
    parts.append("Diagnose the most likely cause of this loss and propose one change.")
    return "\n".join(parts)


async def _generate_narrative(prompt: str, symbol: str) -> tuple[str | None, str | None]:
    # / reuses the groq 70b -> cerebras 70b -> groq 120b -> cerebras 120b chain
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        logger.info("post_mortem_no_groq_key_using_template", symbol=symbol)
        return None, None

    # / primary provider is weighted by LLM_PROVIDER_SPLIT; cerebras-primary reverses the pairs
    from src.data.llm_client import build_fallback_chain
    attempts: list[tuple[str, str]] = build_fallback_chain(
        groq_fast=DEFAULT_MODEL, cerebras_fast=CEREBRAS_FAST_MODEL,
        groq_slow=FALLBACK_MODEL, cerebras_slow=CEREBRAS_MODEL,
    )
    for provider, model in attempts:
        try:
            if provider == "groq":
                result = await _call_llm(
                    api_key, model, prompt, symbol,
                    system_message=_POST_MORTEM_SYSTEM_MSG,
                )
            else:
                result = await _call_cerebras(
                    prompt, symbol, _POST_MORTEM_SYSTEM_MSG, model=model,
                )
            if result and result.summary:
                return result.summary, model
        except _RateLimited:
            continue
        except Exception as exc:
            logger.info("post_mortem_llm_attempt_failed", model=model, error=str(exc)[:120])
            continue
    return None, None


def _template_narrative(
    symbol: str, strategy_id: str, pnl: float, trigger_type: str,
) -> str:
    # / minimal template when all llms are unavailable
    return (
        f"Trade on {symbol} by {strategy_id} closed at PnL {pnl:.2f} "
        f"via trigger {trigger_type}. LLM narrative unavailable — "
        "review strategy entry/exit conditions and regime alignment."
    )


def _compose_markdown(
    symbol: str,
    strategy_id: str,
    pnl: float,
    trigger_type: str,
    deviation_sigma: float | None,
    narrative: str,
    model_used: str | None,
    trade: dict[str, Any] | None,
) -> str:
    # / build the markdown body for the wiki document
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = [
        f"# Post-Mortem: {strategy_id} on {symbol}",
        "",
        f"- **Date (UTC):** {timestamp}",
        f"- **Strategy:** {strategy_id}",
        f"- **Symbol:** {symbol}",
        f"- **Realized PnL:** {pnl:.2f}",
        f"- **Trigger:** {trigger_type}",
    ]
    if deviation_sigma is not None:
        lines.append(f"- **Deviation (sigma):** {deviation_sigma:.2f}")
    if model_used:
        lines.append(f"- **Narrative source:** {model_used}")
    else:
        lines.append("- **Narrative source:** template fallback")

    if trade:
        lines.append("")
        lines.append("## Closing Trade")
        for key in ("side", "qty", "price", "order_id", "broker", "regime"):
            val = trade.get(key)
            if val is not None:
                lines.append(f"- {key}: {val}")

    lines.append("")
    lines.append("## Narrative")
    lines.append(narrative.strip())
    lines.append("")
    lines.append("## Lessons")
    lines.append("_(auto-generated — evolution engine will consume this via wiki context)_")

    return "\n".join(lines) + "\n"


def _load_strategy_config(strategy_id: str) -> dict | None:
    # / read the raw json config off disk to avoid pydantic roundtrip cost
    import re as _re
    from src.strategies.strategy_loader import CONFIGS_DIR

    if not strategy_id or not _re.match(r"^[a-zA-Z0-9_-]+$", strategy_id):
        return None
    path = CONFIGS_DIR / f"{strategy_id}.json"
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


async def _fetch_context(
    pool, trade_id: int | None, strategy_id: str, symbol: str,
) -> tuple[dict | None, dict | None, list[dict]]:
    # / gather the trade row, strategy config, and recent trades
    from src.agents.tools import fetch_recent_trades

    trade_row: dict | None = None
    if trade_id is not None:
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM trade_log WHERE id = $1", int(trade_id),
                )
                if row:
                    trade_row = dict(row)
        except Exception as exc:
            logger.info("post_mortem_trade_fetch_failed", error=str(exc)[:120])

    strategy_config = _load_strategy_config(strategy_id)

    try:
        recent = await fetch_recent_trades(pool, strategy_id=strategy_id, limit=10)
    except Exception:
        recent = []

    return trade_row, strategy_config, recent


async def write_post_mortem(
    pool,
    trade_id: int | None,
    strategy_id: str,
    symbol: str,
    pnl: float,
    trigger_type: str,
    deviation_sigma: float | None = None,
) -> bool:
    # / atomic cooldown-and-claim: closes the TOCTOU window a concurrent trade-close could exploit
    if not strategy_id or not symbol:
        logger.info("post_mortem_missing_identifiers",
                    strategy_id=strategy_id, symbol=symbol)
        return False

    cooldown_hours = int(os.environ.get("POST_MORTEM_COOLDOWN_HOURS", "24"))

    # / fetch context first — cheap, read-only; needed for the initial details payload
    trade_row, strategy_config, recent_trades = await _fetch_context(
        pool, trade_id, strategy_id, symbol,
    )

    initial_details = {
        "trade_id": trade_id,
        "trigger_type": trigger_type,
        "has_strategy_config": strategy_config is not None,
        "recent_trade_count": len(recent_trades or []),
    }

    # / atomic claim — inserts a row only if no post_mortem exists for this strategy in cooldown window
    try:
        row_id = await claim_post_mortem_slot(
            pool,
            strategy_id=strategy_id,
            symbol=symbol,
            trigger_type=trigger_type,
            pnl=pnl,
            expected_pnl=None,
            deviation_sigma=deviation_sigma,
            details=initial_details,
            cooldown_hours=cooldown_hours,
        )
    except Exception as exc:
        logger.error("post_mortem_claim_failed", error=str(exc))
        return False

    if row_id is None:
        logger.info(
            "post_mortem_cooldown_blocked",
            strategy_id=strategy_id, symbol=symbol, hours=cooldown_hours,
        )
        return False

    # / slot is claimed — now do the expensive narrative + wiki write
    prompt = _build_prompt(
        symbol=symbol,
        strategy_id=strategy_id,
        pnl=pnl,
        trigger_type=trigger_type,
        deviation_sigma=deviation_sigma,
        trade=trade_row,
        strategy_config=strategy_config,
        recent_trades=recent_trades,
    )

    narrative, model_used = await _generate_narrative(prompt, symbol)
    if not narrative:
        narrative = _template_narrative(symbol, strategy_id, pnl, trigger_type)

    content = _compose_markdown(
        symbol=symbol,
        strategy_id=strategy_id,
        pnl=pnl,
        trigger_type=trigger_type,
        deviation_sigma=deviation_sigma,
        narrative=narrative,
        model_used=model_used,
        trade=trade_row,
    )

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    slug = f"{strategy_id}_{symbol}_{date_str}"
    writer = WikiWriter(pool=pool)

    wiki_path: str | None = None
    try:
        wiki_path = await writer.write_document(
            category="post-mortems",
            filename=slug,
            content=content,
            title=f"Post-mortem: {strategy_id} on {symbol} ({date_str})",
            symbols=[symbol],
            strategy_ids=[strategy_id],
            confidence="emerging",
        )
    except Exception as exc:
        logger.error("post_mortem_wiki_write_failed", error=str(exc))
        wiki_path = None

    # / patch the claimed row with final details + wiki_path
    final_details = dict(initial_details)
    final_details["model_used"] = model_used
    final_details["narrative_bytes"] = len(narrative.encode("utf-8"))

    try:
        await update_post_mortem_details(pool, row_id, final_details)
        await set_post_mortem_wiki_path(pool, row_id, wiki_path)
    except Exception as exc:
        logger.error("post_mortem_row_patch_failed", error=str(exc), row_id=row_id)
        return bool(wiki_path)

    logger.info(
        "post_mortem_written",
        strategy_id=strategy_id, symbol=symbol,
        pnl=pnl, wiki_path=wiki_path, model=model_used,
    )
    return True
