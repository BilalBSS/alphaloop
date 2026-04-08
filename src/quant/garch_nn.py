# / garch(1,1) volatility forecasting with rolling fallback

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import structlog

logger = structlog.get_logger(__name__)


@dataclass
class VolForecast:
    vol_1d: float
    vol_5d: float
    model: str


def forecast_volatility(
    prices: pd.Series,
    window: int = 252,
) -> VolForecast:
    # / forecast volatility using garch with rolling fallback
    log_ret = np.log(prices / prices.shift(1)).dropna()

    if len(log_ret) < 60:
        vol = float(log_ret.tail(20).std() * np.sqrt(252))
        return VolForecast(vol_1d=vol, vol_5d=vol, model="rolling")

    try:
        from arch import arch_model

        returns_pct = log_ret.tail(window) * 100
        am = arch_model(returns_pct, vol="Garch", p=1, q=1, rescale=False)
        res = am.fit(disp="off", show_warning=False)

        fcast = res.forecast(horizon=5)
        var_1d = fcast.variance.iloc[-1, 0]
        var_5d = fcast.variance.iloc[-1, :5].mean()

        vol_1d = float(np.sqrt(var_1d * 252) / 100)
        vol_5d = float(np.sqrt(var_5d * 252) / 100)

        return VolForecast(vol_1d=vol_1d, vol_5d=vol_5d, model="garch")
    except ImportError:
        logger.warning("arch_not_installed")
    except Exception as exc:
        logger.warning("garch_fit_failed", error=str(exc))

    vol_20d = float(log_ret.tail(20).std() * np.sqrt(252))
    vol_5d_simple = float(log_ret.tail(5).std() * np.sqrt(252))
    return VolForecast(vol_1d=vol_20d, vol_5d=vol_5d_simple, model="rolling")
