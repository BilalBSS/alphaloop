
from __future__ import annotations

import json
import os

import structlog

from src.data.llm_client import build_fallback_chain, llm_call

logger = structlog.get_logger(__name__)

GROQ_FAST_MODEL = "llama-3.3-70b-versatile"
CEREBRAS_FAST_MODEL = "llama-3.3-70b"
GROQ_SLOW_MODEL = "llama-3.3-70b-versatile"
CEREBRAS_SLOW_MODEL = "llama-3.3-70b"

_TIMEOUT = 12.0
_MAX_TOKENS = 220

_SYSTEM_MSG = (
    "You are a senior quant analyst writing a one-paragraph lesson for a strategy "
    "playbook. The reader is the next mutation of this strategy and must learn what "
    "trait caused this outcome and what to repeat or avoid.\n"
    "Rules:\n"
    "- Under 90 words\n"
    "- Lead with the single most likely cause\n"
    "- Name the specific trait (entry threshold, exit type, regime, sector exposure)\n"
    "- End with one concrete prescription for the next mutation\n"
    "- No hedging, no preamble, no metric dumps"
)


def _config_brief(config: dict | None) -> str:
    if not config:
        return "(config unavailable)"
    parts: list[str] = []
    for key in (
        "name", "asset_class", "tier", "sector", "symbol",
        "entry_conditions", "exit_conditions", "position_sizing",
    ):
        val = config.get(key)
        if val is None:
            continue
        text = json.dumps(val, default=str) if not isinstance(val, str) else val
        parts.append(f"  {key}: {text[:240]}")
    return "\n".join(parts) if parts else "(empty config)"


def _trades_brief(trades: list[dict] | None) -> str:
    if not trades:
        return "(no recent trades)"
    lines: list[str] = []
    for t in trades[:8]:
        lines.append(
            f"  - {t.get('symbol', '?')} {t.get('side', '?')} "
            f"pnl={t.get('pnl')} regime={t.get('regime', '?')} "
            f"at={t.get('created_at', '?')}"
        )
    return "\n".join(lines)


def build_kill_prompt(
    strategy_id: str,
    config: dict | None,
    sharpe: float | None,
    trade_count: int | None,
    composite: float | None,
    days_alive: int | None,
    reason: str,
    recent_trades: list[dict] | None,
    regime: str | None,
) -> str:
    parts = [
        f"Strategy {strategy_id} was killed.",
        f"Reason: {reason}",
    ]
    if sharpe is not None:
        parts.append(f"Sharpe: {sharpe:.3f}")
    if composite is not None:
        parts.append(f"Composite: {composite:.4f}")
    if trade_count is not None:
        parts.append(f"Trades: {trade_count}")
    if days_alive is not None:
        parts.append(f"Days alive: {days_alive}")
    if regime:
        parts.append(f"Last regime: {regime}")
    parts.append("\n## Strategy Config")
    parts.append(_config_brief(config))
    parts.append("\n## Recent Trades")
    parts.append(_trades_brief(recent_trades))
    parts.append("\n## Task")
    parts.append(
        "What trait of this strategy most likely caused the failure? "
        "What should the next mutation try instead?"
    )
    return "\n".join(parts)


def build_promotion_prompt(
    strategy_id: str,
    config: dict | None,
    sharpe: float,
    win_rate: float,
    paper_days: int,
    trade_count: int | None,
    recent_trades: list[dict] | None,
    regime: str | None,
) -> str:
    parts = [
        f"Strategy {strategy_id} was promoted from paper to live.",
        f"Sharpe: {sharpe:.3f}",
        f"Win rate: {win_rate:.2%}",
        f"Paper days: {paper_days}",
    ]
    if trade_count is not None:
        parts.append(f"Trades: {trade_count}")
    if regime:
        parts.append(f"Current regime: {regime}")
    parts.append("\n## Strategy Config")
    parts.append(_config_brief(config))
    parts.append("\n## Recent Trades")
    parts.append(_trades_brief(recent_trades))
    parts.append("\n## Task")
    parts.append(
        "What trait of this strategy most likely drove the success? "
        "What should future mutations preserve and amplify?"
    )
    return "\n".join(parts)


def build_mutation_prompt(
    parent_id: str,
    mutant_id: str,
    parent_config: dict | None,
    mutant_config: dict | None,
    parent_sharpe: float | None,
    mutant_sharpe: float,
    composite: float,
    sharpe_delta: float | None,
    wiki_guided: bool,
) -> str:
    parts = [
        f"Mutation: parent {parent_id} -> mutant {mutant_id}",
        f"Mutant Sharpe: {mutant_sharpe:.3f} (composite {composite:.4f})",
    ]
    if parent_sharpe is not None:
        parts.append(f"Parent Sharpe: {parent_sharpe:.3f}")
    if sharpe_delta is not None:
        parts.append(f"Sharpe delta: {sharpe_delta:+.3f}")
    parts.append(f"Wiki-guided: {wiki_guided}")
    parts.append("\n## Parent Config")
    parts.append(_config_brief(parent_config))
    parts.append("\n## Mutant Config")
    parts.append(_config_brief(mutant_config))
    parts.append("\n## Task")
    parts.append(
        "What changed between parent and mutant? Did the change help or hurt? "
        "What does this teach about future mutations of this lineage?"
    )
    return "\n".join(parts)


async def _call_chain(prompt: str, label: str) -> str | None:
    if not os.environ.get("GROQ_API_KEY") and not os.environ.get("CEREBRAS_API_KEY"):
        return None

    chain = build_fallback_chain(
        groq_fast=GROQ_FAST_MODEL, cerebras_fast=CEREBRAS_FAST_MODEL,
        groq_slow=GROQ_SLOW_MODEL, cerebras_slow=CEREBRAS_SLOW_MODEL,
    )
    messages = [
        {"role": "system", "content": _SYSTEM_MSG},
        {"role": "user", "content": prompt},
    ]
    for provider, model in chain:
        try:
            data = await llm_call(
                provider=provider, messages=messages, model=model,
                timeout=_TIMEOUT, max_tokens=_MAX_TOKENS, temperature=0.4,
            )
            choices = data.get("choices") or []
            if not choices:
                continue
            content = (choices[0].get("message") or {}).get("content") or ""
            content = content.strip()
            if content:
                logger.info("lesson_distilled", label=label, provider=provider, model=model, chars=len(content))
                return content
        except Exception as exc:
            logger.info("lesson_distill_attempt_failed", label=label, provider=provider, model=model, error=str(exc)[:140])
            continue
    return None


def _template_kill(strategy_id: str, sharpe: float | None, reason: str, regime: str | None) -> str:
    base = f"Strategy {strategy_id} killed: {reason}."
    if sharpe is not None:
        base += f" Final Sharpe {sharpe:.2f}."
    if regime:
        base += f" Last regime: {regime}."
    base += " Next mutation: tighten entry conditions or pick a different regime."
    return base


def _template_promotion(strategy_id: str, sharpe: float, win_rate: float, paper_days: int) -> str:
    return (
        f"Strategy {strategy_id} promoted after {paper_days}d paper trading "
        f"(Sharpe {sharpe:.2f}, win rate {win_rate:.0%}). "
        "Preserve current entry/exit logic in future mutations."
    )


def _template_mutation(
    mutant_id: str, composite: float, mutant_sharpe: float, sharpe_delta: float | None, wiki_guided: bool,
) -> str:
    base = f"Mutation {mutant_id} scored composite {composite:.4f} (Sharpe {mutant_sharpe:.2f})"
    if sharpe_delta is not None:
        direction = "improvement" if sharpe_delta > 0 else "regression"
        base += f", {direction} {sharpe_delta:+.2f} vs parent"
    base += f". Wiki-guided: {wiki_guided}."
    return base


async def distill_kill(
    strategy_id: str,
    config: dict | None,
    sharpe: float | None,
    trade_count: int | None,
    composite: float | None,
    days_alive: int | None,
    reason: str,
    recent_trades: list[dict] | None = None,
    regime: str | None = None,
) -> str:
    prompt = build_kill_prompt(
        strategy_id, config, sharpe, trade_count, composite, days_alive, reason, recent_trades, regime,
    )
    distilled = await _call_chain(prompt, label="kill")
    if distilled:
        return distilled
    return _template_kill(strategy_id, sharpe, reason, regime)


async def distill_promotion(
    strategy_id: str,
    config: dict | None,
    sharpe: float,
    win_rate: float,
    paper_days: int,
    trade_count: int | None = None,
    recent_trades: list[dict] | None = None,
    regime: str | None = None,
) -> str:
    prompt = build_promotion_prompt(
        strategy_id, config, sharpe, win_rate, paper_days, trade_count, recent_trades, regime,
    )
    distilled = await _call_chain(prompt, label="promotion")
    if distilled:
        return distilled
    return _template_promotion(strategy_id, sharpe, win_rate, paper_days)


async def distill_mutation(
    parent_id: str,
    mutant_id: str,
    parent_config: dict | None,
    mutant_config: dict | None,
    parent_sharpe: float | None,
    mutant_sharpe: float,
    composite: float,
    sharpe_delta: float | None,
    wiki_guided: bool,
) -> str:
    prompt = build_mutation_prompt(
        parent_id, mutant_id, parent_config, mutant_config,
        parent_sharpe, mutant_sharpe, composite, sharpe_delta, wiki_guided,
    )
    distilled = await _call_chain(prompt, label="mutation")
    if distilled:
        return distilled
    return _template_mutation(mutant_id, composite, mutant_sharpe, sharpe_delta, wiki_guided)
