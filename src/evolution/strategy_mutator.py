# / claude haiku strategy mutation
# / takes a killed strategy config and top performer, proposes a new config
# / falls back to random parameter tweak if llm fails

from __future__ import annotations

import json
import os
import re
import uuid
from typing import Any

import numpy as np
import structlog

logger = structlog.get_logger(__name__)

# / parameters eligible for random tweaking
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
) -> list[dict]:
    # / propose mutated strategy configs using haiku + deepseek reasoner critique
    # / returns list[dict]: usually 1, sometimes 2 on disagreement
    # / falls back to random tweak if llm unavailable or fails
    rng = rng or np.random.default_rng()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.info("no_anthropic_key_using_random_tweak")
        return [_random_tweak(killed_config, rng)]

    # / 1. haiku proposes
    haiku_config = await _haiku_propose(api_key, killed_config, top_config, recent_trades, rng)

    # / 2. deepseek reasoner critiques
    critique = await _reasoner_critique(haiku_config, killed_config, top_config, recent_trades)

    if critique["decision"] == "approve":
        logger.info("reasoner_approved", strategy_id=haiku_config.get("id"))
        return [haiku_config]
    elif critique["decision"] == "reject" and critique.get("alternative"):
        logger.info("reasoner_disagreed", strategy_id=haiku_config.get("id"))
        return [haiku_config, critique["alternative"]]
    else:
        # / reject without alternative, or error/timeout
        return [haiku_config]


async def _haiku_propose(
    api_key: str,
    killed_config: dict,
    top_config: dict,
    recent_trades: list[dict],
    rng: np.random.Generator,
) -> dict:
    # / haiku mutation with 3-retry loop (existing logic extracted)
    prompt = _build_mutation_prompt(killed_config, top_config, recent_trades)

    for attempt in range(3):
        try:
            response_text = await _call_haiku(api_key, prompt)
            config = _parse_json_response(response_text)

            from src.strategies.strategy_loader import validate_config
            validate_config(config)

            config["id"] = f"strategy_{uuid.uuid4().hex[:8]}"
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

            logger.info("haiku_mutation_success", new_id=config["id"], parent=config["parent_id"])
            return config

        except Exception as exc:
            logger.warning("haiku_attempt_failed", attempt=attempt + 1, error=str(exc))
            if attempt < 2:
                prompt += f"\n\nPrevious attempt failed: {exc}. Fix the JSON and try again."

    logger.warning("haiku_all_attempts_failed_using_random_tweak")
    return _random_tweak(killed_config, rng)


async def _reasoner_critique(
    haiku_config: dict,
    killed_config: dict,
    top_config: dict,
    recent_trades: list[dict],
) -> dict:
    # / deepseek reasoner reviews haiku's proposal
    # / returns {decision: "approve"|"reject", reason: str, alternative: dict|None}
    deepseek_key = os.environ.get("DEEPSEEK_API_KEY")
    if not deepseek_key:
        logger.info("no_deepseek_key_skipping_critique")
        return {"decision": "approve", "reason": "no api key"}

    try:
        import httpx

        trades_summary = ""
        for t in recent_trades[:5]:
            trades_summary += f"  - {t.get('symbol', '?')} {t.get('side', '?')}: pnl={t.get('pnl', '?')}\n"

        prompt = f"""You are a quantitative strategy reviewer. A mutation was proposed by another AI. Review it.

KILLED STRATEGY (was performing poorly):
{json.dumps(killed_config, indent=2)}

PROPOSED MUTATION:
{json.dumps(haiku_config, indent=2)}

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

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://api.deepseek.com/chat/completions",
                headers={"Authorization": f"Bearer {deepseek_key}"},
                json={
                    "model": "deepseek-reasoner",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 3000,
                },
            )
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"]

        result = _parse_json_response(text)

        # / validate alternative if present
        if result.get("alternative"):
            from src.strategies.strategy_loader import validate_config
            validate_config(result["alternative"])
            alt = result["alternative"]
            alt["id"] = f"strategy_{uuid.uuid4().hex[:8]}"
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


async def _call_haiku(api_key: str, prompt: str) -> str:
    # / call claude haiku via anthropic api
    try:
        from anthropic import AsyncAnthropic
    except ImportError:
        raise RuntimeError("anthropic package not installed")

    client = AsyncAnthropic(api_key=api_key)
    response = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2000,
        temperature=0.7,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


def _build_mutation_prompt(
    killed_config: dict, top_config: dict, recent_trades: list[dict],
) -> str:
    # / construct the llm mutation prompt
    trades_summary = ""
    for t in recent_trades[:5]:
        trades_summary += f"  - {t.get('symbol', '?')} {t.get('side', '?')}: pnl={t.get('pnl', '?')}\n"

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
    # / extract json from llm response, handles markdown fences
    text = text.strip()

    # / try to extract from code fence
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        text = match.group(1).strip()

    return json.loads(text)


def _random_tweak(config: dict, rng: np.random.Generator) -> dict:
    # / deterministic fallback: randomly adjust one parameter
    import copy
    new_config = copy.deepcopy(config)

    # / assign new identity
    new_config["id"] = f"strategy_{uuid.uuid4().hex[:8]}"
    new_config["parent_id"] = config.get("id", "unknown")
    new_config["created_by"] = "random_mutation"
    new_config["version"] = 1
    if "metadata" not in new_config:
        new_config["metadata"] = {}
    new_config["metadata"]["status"] = "backtest_pending"
    new_config["metadata"]["generation"] = config.get("metadata", {}).get("generation", 0) + 1

    # / pick a random signal to tweak
    signals = new_config.get("entry_conditions", {}).get("signals", [])
    if signals:
        idx = int(rng.integers(0, len(signals)))
        signal = signals[idx]

        # / tweak period if present and not None
        if signal.get("period") is not None:
            delta = int(rng.integers(-3, 4))
            signal["period"] = max(2, signal["period"] + delta)

        # / tweak threshold if present and not None
        if signal.get("threshold") is not None:
            delta = float(rng.uniform(-5, 5))
            signal["threshold"] = round(signal["threshold"] + delta, 1)

        # / tweak multiplier if present and not None
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
