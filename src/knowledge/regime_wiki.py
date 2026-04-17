# / wiki writer for regime shifts — composes markdown + inserts regime_shifts row

from __future__ import annotations

from datetime import datetime, timezone

import structlog

from src.knowledge.db_helpers import store_regime_shift_row
from src.knowledge.wiki_writer import WikiWriter

logger = structlog.get_logger(__name__)


def _compose_markdown(
    old_regime: str,
    new_regime: str,
    market: str,
    confidence: float | None,
) -> str:
    # / build the regime shift note for the wiki
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = [
        f"# Regime Shift: {old_regime} -> {new_regime}",
        "",
        f"- **Date (UTC):** {timestamp}",
        f"- **Market:** {market}",
        f"- **From:** {old_regime}",
        f"- **To:** {new_regime}",
    ]
    if confidence is not None:
        lines.append(f"- **Detector confidence:** {confidence:.2f}")
    lines.append("")
    lines.append("## Summary")
    lines.append(
        f"The {market} regime flipped from **{old_regime}** to **{new_regime}**. "
        "Strategies that performed well in the old regime should be monitored for decay; "
        "mutation proposals should bias toward configs that historically worked in the new regime."
    )
    lines.append("")
    lines.append("## Next Steps")
    lines.append("- Evolution engine picks this up via WikiContext.get_mutation_context")
    lines.append("- Post-mortems generated under the new regime will live-update the playbook")
    return "\n".join(lines) + "\n"


async def on_regime_shift(
    pool,
    old_regime: str,
    new_regime: str,
    confidence: float | None = None,
    market: str = "equity",
) -> str | None:
    # / persist a regime-shift markdown + structured row; returns wiki path or none
    if not old_regime or not new_regime or old_regime == new_regime:
        logger.info(
            "regime_shift_skipped_invalid",
            old=old_regime, new=new_regime, market=market,
        )
        return None

    content = _compose_markdown(old_regime, new_regime, market, confidence)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    slug = f"{new_regime}_{market}_{date_str}".replace(" ", "_")

    writer = WikiWriter(pool=pool)
    wiki_path: str | None = None
    try:
        wiki_path = await writer.write_document(
            category="regimes",
            filename=slug,
            content=content,
            title=f"Regime shift: {market} {old_regime} -> {new_regime} ({date_str})",
            confidence="emerging",
        )
    except Exception as exc:
        logger.error("regime_wiki_write_failed", error=str(exc))

    try:
        await store_regime_shift_row(
            pool,
            old_regime=old_regime,
            new_regime=new_regime,
            market=market,
            confidence=confidence,
            wiki_path=wiki_path,
        )
    except Exception as exc:
        logger.error("regime_shift_row_insert_failed", error=str(exc))

    logger.info(
        "regime_shift_recorded",
        old=old_regime, new=new_regime, market=market, wiki_path=wiki_path,
    )
    return wiki_path
