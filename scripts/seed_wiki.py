#!/usr/bin/env python3
# / one-time wiki seeder: regimes, meta, strategy playbooks, symbol profiles
# / idempotent — safe to run multiple times (overwrites existing files)
# / usage: python -m scripts.seed_wiki

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# / add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

import structlog

from src.data.db import close_db, init_db
from src.data.symbols import (
    CRYPTO_UNIVERSE,
    EQUITY_UNIVERSE,
    FULL_UNIVERSE,
    SECTORS,
    get_sector,
    is_crypto,
)
from src.knowledge.wiki_writer import WikiWriter
from src.strategies.strategy_loader import CONFIGS_DIR

logger = structlog.get_logger(__name__)


REGIME_DOCS: dict[str, tuple[str, str]] = {
    "bull": (
        "Bull Regime Playbook",
        (
            "Market is in an uptrend with shallow drawdowns (<10%) and moderate volatility.\n\n"
            "## What Works\n"
            "- Trend-following: SMA cross-overs, momentum breakouts\n"
            "- Buying dips inside 200-day SMA\n"
            "- Heavier position sizing (still under 8%/position)\n\n"
            "## What Breaks\n"
            "- Mean reversion into weakness\n"
            "- Short strategies — trending market punishes them\n"
            "- Overreacting to single down-days\n\n"
            "## Evolution Hints\n"
            "- Prefer wider stops, longer holding windows\n"
            "- Relax fundamental filters slightly — quality is rewarded"
        ),
    ),
    "bear": (
        "Bear Regime Playbook",
        (
            "Market is in a downtrend with drawdowns >15% from highs. Expect persistent selling.\n\n"
            "## What Works\n"
            "- Tight risk controls, smaller sizes\n"
            "- Quality-focused fundamentals (FCF margin, low debt)\n"
            "- Mean reversion on capitulation selloffs\n\n"
            "## What Breaks\n"
            "- Momentum/breakout strategies\n"
            "- Any strategy that assumed trend persistence from bull regime\n"
            "- Buying the dip without fundamental confirmation\n\n"
            "## Evolution Hints\n"
            "- Use bear_market_overrides to bypass strict consensus gating\n"
            "- Narrow universes to large caps / liquid crypto\n"
            "- Consider shorter holding windows"
        ),
    ),
    "sideways": (
        "Sideways Regime Playbook",
        (
            "Market is range-bound without a strong directional trend.\n\n"
            "## What Works\n"
            "- Mean reversion strategies (Bollinger band plays)\n"
            "- RSI oversold / overbought bounces\n"
            "- Support/resistance respecters\n\n"
            "## What Breaks\n"
            "- Trend-following breaks out prematurely and reverses\n"
            "- Wide stops get chopped\n\n"
            "## Evolution Hints\n"
            "- Tighten stops, tighter take-profits\n"
            "- Volume confirmation more important than usual"
        ),
    ),
    "high_vol": (
        "High Volatility Regime Playbook",
        (
            "Realized volatility is 2x+ the median baseline. Price swings wide on any news.\n\n"
            "## What Works\n"
            "- Volatility-normalized position sizing (ATR-based)\n"
            "- Wider stops to avoid noise shakeouts\n"
            "- Contrarian plays on extreme sentiment\n\n"
            "## What Breaks\n"
            "- Fixed-percent stops (get whipsawed)\n"
            "- Over-sized positions\n"
            "- Strategies that don't normalize by volatility\n\n"
            "## Evolution Hints\n"
            "- Favor ATR stops over fixed stops\n"
            "- Reduce max_position_pct\n"
            "- Consider shorter time_exit"
        ),
    ),
}


META_KNOWN_ISSUES_BODY = (
    "Tracked system-level biases and known issues that should inform strategy evolution.\n\n"
    "## Known Biases\n"
    "- DCF model can produce extreme upside/downside (>50%) when terminal multiples aren't reviewed\n"
    "- yfinance fundamentals can be stale up to 24h\n"
    "- Insider signals are 1-3 days delayed (SEC Form 4 filing lag)\n"
    "- Paper broker simulation assumes perfect fills — live slippage may differ\n\n"
    "## Known Issues\n"
    "- Regime detection uses 20-day vol window — may lag transitions by several days\n"
    "- Analyst prompts do not yet incorporate chart vision signals (Track V / Phase 3)\n"
    "- Wiki context injection is evolution-only — analyst stays stateless\n\n"
    "## Rules for Strategy Mutation\n"
    "- Never exceed 10% position size\n"
    "- Dual-track: fundamental-gated (>=2 signals + filters) vs momentum-only (>=1 signal, <=4%)\n"
    "- Fix the failure mode before copying the top performer's surface features"
)


def _strategy_playbook_stub(strategy_id: str, config: dict) -> str:
    # / compose a baseline playbook from the strategy config
    name = config.get("name", strategy_id)
    description = config.get("description", "(no description)")
    asset_class = config.get("asset_class", "unknown")
    universe = config.get("universe", "unknown")
    tier = config.get("tier", "unknown")
    symbols: list[str] = []
    if config.get("symbol"):
        symbols.append(str(config["symbol"]))
    sector = config.get("sector")

    entry = config.get("entry_conditions", {})
    exit_rules = config.get("exit_conditions", {})
    sizing = config.get("position_sizing", {})
    fundamentals = config.get("fundamental_filters")

    lines: list[str] = [
        f"# {name} Playbook",
        "",
        f"*Strategy id:* `{strategy_id}`",
        f"*Asset class:* {asset_class}",
        f"*Universe:* {universe}",
        f"*Tier:* {tier}",
    ]
    if sector:
        lines.append(f"*Sector:* {sector}")
    if symbols:
        lines.append(f"*Target symbol:* {symbols[0]}")
    lines.extend([
        "",
        "## Description",
        description,
        "",
        "## Entry Conditions",
        f"```json\n{json.dumps(entry, indent=2)}\n```",
        "",
        "## Exit Conditions",
        f"```json\n{json.dumps(exit_rules, indent=2)}\n```",
        "",
        "## Position Sizing",
        f"```json\n{json.dumps(sizing, indent=2)}\n```",
    ])
    if fundamentals:
        lines.extend([
            "",
            "## Fundamental Filters",
            f"```json\n{json.dumps(fundamentals, indent=2)}\n```",
        ])
    lines.extend([
        "",
        "## Lessons",
        "_Evolution engine will append mutation results and kills here as they happen._",
    ])
    return "\n".join(lines) + "\n"


def _symbol_profile_stub(symbol: str) -> str:
    # / short seed profile per symbol — living doc the system extends
    sector = get_sector(symbol) or "uncategorized"
    market = "crypto" if is_crypto(symbol) else "equity"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines: list[str] = [
        f"# {symbol} Profile",
        "",
        f"*Market:* {market}",
        f"*Sector:* {sector}",
        f"*Profile seeded:* {today}",
        "",
        "## Known Traits",
        "_Seed stub — profile will be enriched by analyst + post-mortem activity._",
        "",
        "## Strategy Fit",
        "- (to be discovered by evolution)",
        "",
        "## Watch For",
        "- (post-mortems and regime shifts will populate this section)",
    ]
    return "\n".join(lines) + "\n"


async def _write_regime_docs(writer: WikiWriter) -> int:
    # / overwrite the four canonical regime playbooks
    written = 0
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for regime_name, (title, body) in REGIME_DOCS.items():
        content = f"# {title}\n\n*Last seeded:* {today}\n\n{body}\n"
        await writer.write_document(
            category="regimes",
            filename=f"{regime_name}_playbook",
            content=content,
            title=f"{title}",
            confidence="established",
        )
        written += 1
    return written


async def _write_meta(writer: WikiWriter) -> int:
    # / meta/known-issues.md consumed by WikiContext.get_mutation_context
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    content = f"# Known Issues\n\n*Last seeded:* {today}\n\n{META_KNOWN_ISSUES_BODY}\n"
    await writer.write_document(
        category="meta",
        filename="known-issues",
        content=content,
        title="Known system issues & biases",
        confidence="established",
    )
    return 1


async def _write_strategy_playbooks(writer: WikiWriter) -> int:
    # / one playbook stub per strategy config on disk
    configs_dir: Path = CONFIGS_DIR
    if not configs_dir.exists():
        logger.warning("seed_wiki_no_configs_dir", path=str(configs_dir))
        return 0
    written = 0
    for config_path in sorted(configs_dir.glob("*.json")):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
        except Exception as exc:
            logger.warning(
                "seed_wiki_bad_config", path=str(config_path), error=str(exc)[:120],
            )
            continue
        sid = config.get("id") or config_path.stem
        content = _strategy_playbook_stub(sid, config)
        strategy_ids = [sid]
        symbols_meta: list[str] = []
        if config.get("symbol"):
            symbols_meta.append(str(config["symbol"]))
        await writer.write_document(
            category="strategies",
            filename=f"{sid}",
            content=content,
            title=f"{config.get('name', sid)} playbook",
            symbols=symbols_meta,
            strategy_ids=strategy_ids,
            confidence="emerging",
        )
        written += 1
    return written


async def _write_symbol_profiles(writer: WikiWriter) -> int:
    # / one profile stub per symbol in FULL_UNIVERSE
    written = 0
    for symbol in FULL_UNIVERSE:
        slug = symbol.replace("/", "_").replace("-", "_").lower()
        content = _symbol_profile_stub(symbol)
        await writer.write_document(
            category="symbols",
            filename=slug,
            content=content,
            title=f"{symbol} profile",
            symbols=[symbol],
            confidence="emerging",
        )
        written += 1
    return written


async def seed() -> dict[str, int]:
    pool = await init_db()
    writer = WikiWriter(pool=pool)
    try:
        regimes = await _write_regime_docs(writer)
        meta = await _write_meta(writer)
        strategies = await _write_strategy_playbooks(writer)
        symbols = await _write_symbol_profiles(writer)
    finally:
        await close_db()
    summary = {
        "regimes": regimes,
        "meta": meta,
        "strategies": strategies,
        "symbols": symbols,
        "equity_universe": len(EQUITY_UNIVERSE),
        "crypto_universe": len(CRYPTO_UNIVERSE),
        "sectors": len(SECTORS),
    }
    logger.info("seed_wiki_complete", **summary)
    return summary


def main() -> None:
    summary = asyncio.run(seed())
    print("seed complete:")
    for k, v in summary.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
