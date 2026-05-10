# / deepseek v3 strategy mutation

from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path

import numpy as np
import structlog

from src.data.llm_client import llm_call

logger = structlog.get_logger(__name__)

_SEQ_RE = re.compile(r"^strategy_(\d+)$")


def _next_sequential_id(directory: Path | None = None) -> str:
    # / scan and increment
    config_dir = directory or (Path(__file__).parent.parent.parent / "configs" / "strategies")
    if not config_dir.exists():
        return f"strategy_{uuid.uuid4().hex[:8]}"
    max_n = 0
    for path in config_dir.glob("strategy_*.json"):
        m = _SEQ_RE.match(path.stem)
        if m:
            try:
                max_n = max(max_n, int(m.group(1)))
            except ValueError:
                continue
    if max_n == 0:
        return f"strategy_{uuid.uuid4().hex[:8]}"
    return f"strategy_{max_n + 1:03d}"

_TWEAK_PARAMS = [
    ("entry_conditions.signals[].period", -5, 5, int),
    ("entry_conditions.signals[].threshold", -5, 5, float),
    ("entry_conditions.signals[].multiplier", -0.3, 0.3, float),
    ("exit_conditions.stop_loss.pct", -0.01, 0.01, float),
    ("exit_conditions.stop_loss.multiplier", -0.5, 0.5, float),
    ("exit_conditions.time_exit.max_holding_days", -5, 5, int),
    ("position_sizing.kelly_fraction", -0.05, 0.05, float),
]


async def mutate_strategy(
    killed_config: dict,
    top_config: dict,
    recent_trades: list[dict],
    rng: np.random.Generator | None = None,
    wiki_context: str | None = None,
) -> list[dict]:
    rng = rng or np.random.default_rng()

    api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.info("no_llm_key_using_random_tweak")
        return [_random_tweak(killed_config, rng)]

    # / 1. deepseek v3 proposes
    mutator_config = await _llm_propose(
        api_key, killed_config, top_config, recent_trades, rng,
        wiki_context=wiki_context,
    )

    # / 2. deepseek reasoner critiques
    critique = await _reasoner_critique(mutator_config, killed_config, top_config, recent_trades)

    if critique["decision"] == "approve":
        logger.info("reasoner_approved", strategy_id=mutator_config.get("id"))
        return [mutator_config]
    elif critique["decision"] == "reject" and critique.get("alternative"):
        logger.info("reasoner_disagreed", strategy_id=mutator_config.get("id"))
        return [mutator_config, critique["alternative"]]
    else:
        return [mutator_config]


async def _llm_propose(
    api_key: str,
    killed_config: dict,
    top_config: dict,
    recent_trades: list[dict],
    rng: np.random.Generator,
    wiki_context: str | None = None,
) -> dict:
    prompt = _build_mutation_prompt(
        killed_config, top_config, recent_trades, wiki_context=wiki_context,
    )

    for attempt in range(3):
        try:
            response_text = await _call_deepseek_v3(prompt)
            config = _parse_json_response(response_text)

            from src.strategies.strategy_loader import validate_config
            validated = validate_config(config)
            config = validated.model_dump()

            config["id"] = _next_sequential_id()
            config["parent_id"] = killed_config.get("id", "unknown")
            config["created_by"] = "evolution_agent"
            config["version"] = 1
            if "metadata" not in config:
                config["metadata"] = {}
            config["metadata"]["status"] = "backtest_pending"
            config["metadata"]["generation"] = killed_config.get("metadata", {}).get("generation", 0) + 1

            # / preserve tier/sector/symbol from parent
            for field in ("tier", "sector", "symbol"):
                if field in killed_config:
                    config[field] = killed_config[field]

            logger.info("mutation_success", new_id=config["id"], parent=config["parent_id"])
            return config

        except Exception as exc:
            logger.warning("mutation_attempt_failed", attempt=attempt + 1, error=str(exc))
            if attempt < 2:
                prompt += f"\n\nPrevious attempt failed: {exc}. Fix the JSON and try again."

    logger.warning("mutation_all_attempts_failed_using_random_tweak")
    return _random_tweak(killed_config, rng)


async def _reasoner_critique(
    mutator_config: dict,
    killed_config: dict,
    top_config: dict,
    recent_trades: list[dict],
) -> dict:
    deepseek_key = os.environ.get("DEEPSEEK_API_KEY")
    if not deepseek_key:
        logger.info("no_deepseek_key_skipping_critique")
        return {"decision": "approve", "reason": "no api key"}

    try:
        trades_summary = ""
        for t in recent_trades[:5]:
            trades_summary += f"  - {t.get('symbol', '?')} {t.get('side', '?')}: pnl={t.get('pnl', '?')}\n"

        prompt = f"""You are a quantitative strategy reviewer. A mutation was proposed by another AI. Review it.

KILLED STRATEGY (was performing poorly):
{json.dumps(killed_config, indent=2)}

PROPOSED MUTATION:
{json.dumps(mutator_config, indent=2)}

TOP PERFORMER (reference):
{json.dumps(top_config, indent=2)}

RECENT TRADES:
{trades_summary or "  No recent trades."}

REVIEW THE MUTATION. Consider:
- Does the indicator combination make sense for the asset class?
- Are the parameters reasonable (not overfitting to noise)?
- Does it address why the killed strategy failed?

Output ONLY valid JSON:
{{"decision": "approve" or "reject", "reason": "one sentence why", "alternative": null or a complete strategy config JSON if you have a better idea}}"""

        data = await llm_call(
            "deepseek",
            messages=[{"role": "user", "content": prompt}],
            model="deepseek-reasoner",
            max_tokens=3000,
        )
        text = data["choices"][0]["message"]["content"]

        result = _parse_json_response(text)

        # / validate alternative if present
        if result.get("alternative"):
            from src.strategies.strategy_loader import validate_config
            validate_config(result["alternative"])
            alt = result["alternative"]
            alt["id"] = _next_sequential_id()
            alt["parent_id"] = killed_config.get("id", "unknown")
            alt["created_by"] = "evolution_reasoner"
            alt["version"] = 1
            if "metadata" not in alt:
                alt["metadata"] = {}
            alt["metadata"]["status"] = "backtest_pending"
            alt["metadata"]["generation"] = killed_config.get("metadata", {}).get("generation", 0) + 1
            for field in ("tier", "sector", "symbol"):
                if field in killed_config:
                    alt[field] = killed_config[field]
            result["alternative"] = alt

        logger.info("reasoner_critique", decision=result.get("decision"), reason=result.get("reason"))
        return result

    except Exception as exc:
        logger.warning("reasoner_critique_failed", error=str(exc))
        return {"decision": "approve", "reason": f"critique failed: {exc}"}


async def _call_deepseek_v3(prompt: str) -> str:
    data = await llm_call(
        "deepseek",
        messages=[{"role": "user", "content": prompt}],
        model="deepseek-chat",
        max_tokens=2000,
        temperature=0.7,
    )
    return data["choices"][0]["message"]["content"]


def _build_mutation_prompt(
    killed_config: dict,
    top_config: dict,
    recent_trades: list[dict],
    wiki_context: str | None = None,
) -> str:
    trades_summary = ""
    for t in recent_trades[:5]:
        trades_summary += f"  - {t.get('symbol', '?')} {t.get('side', '?')}: pnl={t.get('pnl', '?')}\n"

    wiki_block = ""
    if wiki_context:
        wiki_block = f"\n## RELEVANT WIKI CONTEXT\n{wiki_context}\n"

    return f"""You are a quantitative strategy optimizer. A trading strategy was killed for poor performance. Your job is to propose a new, improved strategy config.

KILLED STRATEGY (poor performance):
```json
{json.dumps(killed_config, indent=2)}
```

TOP PERFORMING STRATEGY (reference for what works):
```json
{json.dumps(top_config, indent=2)}
```

RECENT TRADES FROM KILLED STRATEGY:
{trades_summary or "  No recent trades."}
{wiki_block}
RULES:
- Output ONLY valid JSON. No explanation, no markdown fences, just the JSON object.
- The config must have: id, name, version, asset_class, universe, entry_conditions (with operator AND/OR and signals array), exit_conditions (with stop_loss), position_sizing.
- entry_conditions.operator must be "AND" or "OR".
- Each signal needs: indicator, condition. Optional: period, lookback, threshold, std_dev, multiplier.
- Valid indicators: bollinger_bands, rsi, macd, volume, sma, adx, atr, stochastic.
- If fundamental_filters are present (pe_ratio_max, revenue_growth_min, etc), max_position_pct <= 0.08 and need >= 2 signals.
- If no fundamental_filters, max_position_pct <= 0.04 and need >= 1 signal.
- max_position_pct must be > 0 and <= 0.10.
- Combine the best elements from the top performer with different parameters.
- Try to fix what went wrong with the killed strategy.

Output the complete JSON config now:"""


def _parse_json_response(text: str) -> dict:
    text = text.strip()

    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        text = match.group(1).strip()

    return json.loads(text)


def _random_tweak(config: dict, rng: np.random.Generator) -> dict:
    import copy
    new_config = copy.deepcopy(config)

    # / assign new identity
    new_config["id"] = _next_sequential_id()
    new_config["parent_id"] = config.get("id", "unknown")
    new_config["created_by"] = "random_mutation"
    new_config["version"] = 1
    if "metadata" not in new_config:
        new_config["metadata"] = {}
    new_config["metadata"]["status"] = "backtest_pending"
    new_config["metadata"]["generation"] = config.get("metadata", {}).get("generation", 0) + 1

    signals = new_config.get("entry_conditions", {}).get("signals", [])
    if signals:
        idx = int(rng.integers(0, len(signals)))
        signal = signals[idx]

        if signal.get("period") is not None:
            delta = int(rng.integers(-3, 4))
            signal["period"] = max(2, signal["period"] + delta)

        if signal.get("threshold") is not None:
            delta = float(rng.uniform(-5, 5))
            signal["threshold"] = round(signal["threshold"] + delta, 1)

        if signal.get("multiplier") is not None:
            delta = float(rng.uniform(-0.3, 0.3))
            signal["multiplier"] = round(max(0.1, signal["multiplier"] + delta), 2)

    # / tweak stop loss
    stop_loss = new_config.get("exit_conditions", {}).get("stop_loss", {})
    if stop_loss.get("pct") is not None:
        delta = float(rng.uniform(-0.01, 0.01))
        stop_loss["pct"] = round(max(0.01, stop_loss["pct"] + delta), 3)

    # / tweak max holding days
    time_exit = new_config.get("exit_conditions", {}).get("time_exit", {})
    if time_exit.get("max_holding_days") is not None:
        delta = int(rng.integers(-3, 4))
        time_exit["max_holding_days"] = max(1, time_exit["max_holding_days"] + delta)

    return new_config
