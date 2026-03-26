# / sensitivity matrix: growth rate x terminal multiple grid
# / shows how fair value changes under different assumptions
# / helps identify which assumptions matter most for the valuation

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np
import structlog

from .dcf_model import DCFAssumptions, run_dcf_simulation

logger = structlog.get_logger(__name__)

# / default grid ranges
DEFAULT_GROWTH_RATES = [-0.05, 0.0, 0.05, 0.10, 0.15, 0.20, 0.25]
DEFAULT_TERMINAL_MULTIPLES = [8.0, 10.0, 12.0, 15.0, 18.0, 20.0, 25.0]


@dataclass
class SensitivityResult:
    symbol: str
    date: date
    current_price: float
    base_fair_value: float
    growth_rates: list[float]
    terminal_multiples: list[float]
    matrix: list[list[float]]  # [growth_idx][multiple_idx] = fair value
    upside_matrix: list[list[float]]  # same shape, but upside % vs current price
    most_sensitive_to: str  # "growth" or "terminal_multiple"
    details: dict[str, Any] = field(default_factory=dict)


def compute_sensitivity_matrix(
    assumptions: DCFAssumptions,
    current_price: float,
    growth_rates: list[float] | None = None,
    terminal_multiples: list[float] | None = None,
    num_simulations: int = 1000,
    rng: np.random.Generator | None = None,
) -> tuple[list[list[float]], list[list[float]]]:
    # / returns (value_matrix, upside_matrix)
    # / each cell = median fair value from a small monte carlo run
    growth_rates = growth_rates or DEFAULT_GROWTH_RATES
    terminal_multiples = terminal_multiples or DEFAULT_TERMINAL_MULTIPLES
    rng = rng or np.random.default_rng()

    value_matrix: list[list[float]] = []
    upside_matrix: list[list[float]] = []

    for g in growth_rates:
        row_values: list[float] = []
        row_upside: list[float] = []

        for tm in terminal_multiples:
            # / override growth and terminal for this cell
            cell_assumptions = DCFAssumptions(
                revenue=assumptions.revenue,
                fcf_margin=assumptions.fcf_margin,
                revenue_growth=g,
                growth_std=assumptions.growth_std * 0.5,  # tighter for sensitivity
                margin_std=assumptions.margin_std * 0.5,
                terminal_multiple=tm,
                terminal_multiple_std=assumptions.terminal_multiple_std * 0.3,
                discount_rate=assumptions.discount_rate,
                projection_years=assumptions.projection_years,
                terminal_growth=assumptions.terminal_growth,
                shares_outstanding=assumptions.shares_outstanding,
                net_debt=assumptions.net_debt,
            )

            values = run_dcf_simulation(cell_assumptions, num_simulations, rng)
            median = float(np.median(values))
            upside = (median - current_price) / current_price if current_price > 0 else 0.0

            row_values.append(round(median, 2))
            row_upside.append(round(upside, 4))

        value_matrix.append(row_values)
        upside_matrix.append(row_upside)

    return value_matrix, upside_matrix


def determine_sensitivity_driver(
    value_matrix: list[list[float]],
    growth_rates: list[float],
    terminal_multiples: list[float],
) -> str:
    # / determine whether valuation is more sensitive to growth or terminal multiple
    # / compare range across rows (growth varies) vs columns (multiple varies)
    arr = np.array(value_matrix)

    # / range across growth rates (fix terminal multiple, vary growth)
    growth_ranges = []
    for col in range(arr.shape[1]):
        col_values = arr[:, col]
        growth_ranges.append(np.ptp(col_values))
    avg_growth_range = np.mean(growth_ranges)

    # / range across terminal multiples (fix growth, vary multiple)
    tm_ranges = []
    for row in range(arr.shape[0]):
        row_values = arr[row, :]
        tm_ranges.append(np.ptp(row_values))
    avg_tm_range = np.mean(tm_ranges)

    return "growth" if avg_growth_range > avg_tm_range else "terminal_multiple"


def analyze_sensitivity(
    symbol: str,
    assumptions: DCFAssumptions,
    current_price: float,
    as_of: date | None = None,
    growth_rates: list[float] | None = None,
    terminal_multiples: list[float] | None = None,
    num_simulations: int = 1000,
    rng: np.random.Generator | None = None,
) -> SensitivityResult:
    # / full sensitivity analysis
    as_of = as_of or date.today()
    growth_rates = growth_rates or DEFAULT_GROWTH_RATES
    terminal_multiples = terminal_multiples or DEFAULT_TERMINAL_MULTIPLES

    # / base case fair value
    base_values = run_dcf_simulation(assumptions, num_simulations, rng)
    base_fair_value = round(float(np.median(base_values)), 2)

    # / sensitivity grid
    value_matrix, upside_matrix = compute_sensitivity_matrix(
        assumptions, current_price, growth_rates, terminal_multiples,
        num_simulations, rng,
    )

    driver = determine_sensitivity_driver(value_matrix, growth_rates, terminal_multiples)

    logger.info(
        "sensitivity_analysis_complete",
        symbol=symbol,
        base_value=base_fair_value,
        most_sensitive=driver,
    )

    return SensitivityResult(
        symbol=symbol,
        date=as_of,
        current_price=current_price,
        base_fair_value=base_fair_value,
        growth_rates=growth_rates,
        terminal_multiples=terminal_multiples,
        matrix=value_matrix,
        upside_matrix=upside_matrix,
        most_sensitive_to=driver,
        details={
            "grid_size": f"{len(growth_rates)}x{len(terminal_multiples)}",
            "simulations_per_cell": num_simulations,
        },
    )
