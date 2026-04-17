# / gemini 3 flash vision client for chart analysis
# / 10 rpm token bucket, tolerant json parser, structured ChartAnalysis output

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from src.data.cost_tracker import track_vision_cost

logger = structlog.get_logger(__name__)

DEFAULT_MODEL = "gemini-3-flash"
DEFAULT_RPM = 10
_RPM_WINDOW_SECONDS = 60.0

# / prompt asks for pure json — structured fields we need for chart_analyses row
_ANALYSIS_PROMPT_TEMPLATE = """Analyze this {timeframe} price chart for {symbol}. Output JSON only, no prose, no markdown fences.

Required schema:
{{
  "trend": "bullish" | "bearish" | "sideways",
  "patterns": ["list of patterns detected, e.g. head_and_shoulders, double_bottom, ascending_triangle"],
  "support_levels": [numeric price levels as floats],
  "resistance_levels": [numeric price levels as floats],
  "bullish_score": 0.0 to 1.0 float,
  "analysis_text": "one-paragraph plain-english summary focused on momentum, structure, volume"
}}

Keep support_levels/resistance_levels to at most 3 each, ordered most-significant first. If no clear patterns are visible, return an empty patterns list. Do not include fields outside this schema."""


@dataclass
class ChartAnalysis:
    # / structured gemini vision output for a single chart
    symbol: str
    timeframe: str
    trend: str                       # / bullish | bearish | sideways
    patterns: list[str]
    support_levels: list[float]
    resistance_levels: list[float]
    bullish_score: float             # / 0.0 to 1.0
    analysis_text: str
    model_used: str
    context: str | None = None
    prompt_tokens: int = 0
    output_tokens: int = 0


def _parse_response_json(raw: str) -> dict | None:
    # / tolerant json parser mimicking ai_summary._parse_synthesis_json
    # / handles ```json fences, trailing commas, unescaped control chars, first-{ last-} slice
    if not raw:
        return None
    text = raw.strip()
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        text = match.group(1).strip()

    def _sanitize(s: str) -> str:
        s = re.sub(r",(\s*[}\]])", r"\1", s)

        def _escape_in_string(m: re.Match) -> str:
            inner = m.group(0)
            return inner.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")

        s = re.sub(r'"(?:[^"\\]|\\.)*"', _escape_in_string, s, flags=re.DOTALL)
        s = re.sub(r"[\x00-\x1f]", " ", s)
        return s

    candidates: list[str] = [text, _sanitize(text)]
    brace_start = raw.find("{")
    brace_end = raw.rfind("}")
    if brace_start >= 0 and brace_end > brace_start:
        sliced = raw[brace_start:brace_end + 1]
        candidates.append(sliced)
        candidates.append(_sanitize(sliced))

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, TypeError):
            continue
    return None


def _coerce_floats(values: Any) -> list[float]:
    # / normalize price-level arrays returned by gemini; skip garbage entries
    if not isinstance(values, list):
        return []
    out: list[float] = []
    for v in values:
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            continue
    return out


def _coerce_patterns(values: Any) -> list[str]:
    # / normalize pattern list to string entries only
    if not isinstance(values, list):
        return []
    return [str(v).strip() for v in values if v and isinstance(v, (str, int, float))]


def _coerce_trend(value: Any) -> str:
    # / clamp trend to one of the three buckets
    if not isinstance(value, str):
        return "sideways"
    v = value.strip().lower()
    if v in ("bullish", "bearish", "sideways"):
        return v
    if v in ("up", "bull", "uptrend"):
        return "bullish"
    if v in ("down", "bear", "downtrend"):
        return "bearish"
    return "sideways"


def _coerce_bullish_score(value: Any) -> float:
    # / clamp bullish_score into [0, 1]; accept percent-like inputs
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0.5
    if f > 1.0:
        f = f / 100.0 if f <= 100.0 else 1.0
    return max(0.0, min(1.0, f))


class GeminiVisionClient:
    # / async wrapper around google-generativeai SDK with RPM token bucket

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        rpm: int = DEFAULT_RPM,
    ):
        self._model_name = model or os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        self._rpm = max(1, int(rpm))
        self._call_times: deque[float] = deque(maxlen=self._rpm)
        self._bucket_lock = asyncio.Lock()
        self._sdk_configured = False

    def _configure_sdk(self) -> Any | None:
        # / lazy import + configure the sdk; returns module or None on missing dep
        try:
            import google.generativeai as genai
        except ImportError:
            logger.warning("google_generativeai_not_installed")
            return None
        if not self._sdk_configured:
            genai.configure(api_key=self._api_key)
            self._sdk_configured = True
        return genai

    async def _acquire_slot(self) -> None:
        # / enforce 10 rpm via sliding window — block until oldest call ages out
        async with self._bucket_lock:
            now = time.monotonic()
            while self._call_times and (now - self._call_times[0]) >= _RPM_WINDOW_SECONDS:
                self._call_times.popleft()
            if len(self._call_times) >= self._rpm:
                wait = _RPM_WINDOW_SECONDS - (now - self._call_times[0]) + 0.05
                if wait > 0:
                    logger.info("gemini_rpm_wait", seconds=round(wait, 2))
                    await asyncio.sleep(wait)
                now = time.monotonic()
                while self._call_times and (now - self._call_times[0]) >= _RPM_WINDOW_SECONDS:
                    self._call_times.popleft()
            self._call_times.append(time.monotonic())

    async def analyze_chart(
        self,
        image_path: Path,
        symbol: str,
        timeframe: str,
        context: str | None = None,
    ) -> ChartAnalysis | None:
        # / run gemini vision over a chart png, returns structured analysis or None
        if not self._api_key:
            logger.info("gemini_api_key_missing", symbol=symbol)
            return None
        if not image_path or not Path(image_path).exists():
            logger.warning("gemini_image_missing", symbol=symbol, path=str(image_path))
            return None

        genai = self._configure_sdk()
        if genai is None:
            return None

        prompt_text = _ANALYSIS_PROMPT_TEMPLATE.format(symbol=symbol, timeframe=timeframe)
        if context:
            prompt_text += f"\n\n## Additional context\n{context}"

        await self._acquire_slot()

        try:
            raw = await asyncio.to_thread(
                self._call_sync, genai, image_path, prompt_text,
            )
        except Exception as exc:
            logger.warning(
                "gemini_call_failed",
                symbol=symbol, timeframe=timeframe, error=str(exc)[:200],
            )
            return None

        if not raw:
            logger.warning("gemini_empty_response", symbol=symbol, timeframe=timeframe)
            return None

        parsed = _parse_response_json(raw)
        if parsed is None:
            logger.warning(
                "gemini_bad_json",
                symbol=symbol, timeframe=timeframe, raw_head=raw[:200],
            )
            return None

        trend = _coerce_trend(parsed.get("trend"))
        patterns = _coerce_patterns(parsed.get("patterns"))
        supports = _coerce_floats(parsed.get("support_levels"))[:3]
        resistances = _coerce_floats(parsed.get("resistance_levels"))[:3]
        bullish_score = _coerce_bullish_score(parsed.get("bullish_score"))
        analysis_text = str(parsed.get("analysis_text") or "").strip()
        if not analysis_text:
            logger.warning("gemini_empty_analysis_text", symbol=symbol)
            return None

        # / rough token estimates for cost tracking — prompt chars/4, output chars/4
        prompt_tokens = max(1, len(prompt_text) // 4)
        output_tokens = max(1, len(raw) // 4)
        try:
            track_vision_cost(
                "gemini", self._model_name, images=1,
                in_tokens=prompt_tokens, out_tokens=output_tokens,
            )
        except Exception as exc:
            logger.debug("gemini_cost_track_failed", error=str(exc)[:120])

        return ChartAnalysis(
            symbol=symbol,
            timeframe=timeframe,
            trend=trend,
            patterns=patterns,
            support_levels=supports,
            resistance_levels=resistances,
            bullish_score=bullish_score,
            analysis_text=analysis_text,
            model_used=self._model_name,
            context=context,
            prompt_tokens=prompt_tokens,
            output_tokens=output_tokens,
        )

    def _call_sync(self, genai: Any, image_path: Path, prompt_text: str) -> str:
        # / synchronous sdk call — must run in thread
        # / sdk expects a PIL image or raw bytes blob; use bytes blob to avoid pillow dep
        try:
            from PIL import Image  # type: ignore
            image = Image.open(image_path)
            parts = [prompt_text, image]
        except Exception:
            # / fallback: inline bytes with mime type
            data = Path(image_path).read_bytes()
            parts = [prompt_text, {"mime_type": "image/png", "data": data}]

        model = genai.GenerativeModel(self._model_name)
        response = model.generate_content(parts)
        text = getattr(response, "text", None)
        if text is None:
            # / some sdk versions expose candidates list instead
            try:
                candidates = getattr(response, "candidates", None) or []
                if candidates:
                    content = getattr(candidates[0], "content", None)
                    if content and getattr(content, "parts", None):
                        text = "".join(
                            getattr(p, "text", "") for p in content.parts if hasattr(p, "text")
                        )
            except Exception:
                text = None
        return text or ""
