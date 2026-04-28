# / continuous portfolio correlation monitoring
# / alerts when positions become too correlated (hidden concentration)

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import structlog

logger = structlog.get_logger(__name__)


@dataclass
class CorrelationAlert:
    avg_correlation: float
    max_pair: tuple[str, str]
    max_correlation: float
    high_corr_pairs: list[tuple[str, str, float]]
    is_concentrated: bool


async def check_portfolio_correlation(
    pool,
    positions: list,
    window: int = 20,
    threshold: float = 0.85,
    avg_threshold: float = 0.6,
) -> CorrelationAlert | None:
    # / compute pairwise correlation of held positions
    symbols = [p["symbol"] if isinstance(p, dict) else p.symbol for p in positions]
    if len(symbols) < 2:
        return None

    # / batch fetch all symbols in one query, bucket per-symbol in python
    returns_map = {}
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT symbol, date, close FROM market_data
            WHERE symbol = ANY($1::text[])
            ORDER BY symbol, date DESC""",
            symbols,
        )
    by_symbol: dict[str, list[float]] = {}
    for r in rows:
        by_symbol.setdefault(r["symbol"], []).append(float(r["close"]))
    for symbol, prices_desc in by_symbol.items():
        prices = list(reversed(prices_desc[: window + 1]))
        if len(prices) >= 10:
            rets = np.diff(prices) / np.array(prices[:-1])
            returns_map[symbol] = rets

    if len(returns_map) < 2:
        return None

    # / align to same length
    min_len = min(len(r) for r in returns_map.values())
    aligned = {s: r[-min_len:] for s, r in returns_map.items()}
    syms = list(aligned.keys())

    matrix = np.corrcoef([aligned[s] for s in syms])

    high_pairs = []
    max_corr = -1.0
    max_pair = (syms[0], syms[1])

    correlations = []
    for i in range(len(syms)):
        for j in range(i + 1, len(syms)):
            c = float(matrix[i, j])
            correlations.append(c)
            if c > max_corr:
                max_corr = c
                max_pair = (syms[i], syms[j])
            if c > threshold:
                high_pairs.append((syms[i], syms[j], c))

    avg_corr = float(np.mean(correlations)) if correlations else 0.0

    return CorrelationAlert(
        avg_correlation=avg_corr,
        max_pair=max_pair,
        max_correlation=max_corr,
        high_corr_pairs=high_pairs,
        is_concentrated=avg_corr > avg_threshold,
    )
