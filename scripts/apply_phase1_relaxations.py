#!/usr/bin/env python3
# / one-shot: applies the deep-researcher phase-1 calibration table to each
# / strategy config. every change is annotated with a source so the next
# / reviewer can trace the rationale. safe to re-run — idempotent per field.

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CFG_DIR = ROOT / "configs" / "strategies"


def _find_signal(signals: list[dict], indicator: str, **match) -> dict | None:
    for s in signals:
        if s.get("indicator") != indicator:
            continue
        if all(s.get(k) == v for k, v in match.items()):
            return s
    return None


def _set_signal_field(signals: list[dict], indicator: str, field: str, value):
    sig = _find_signal(signals, indicator)
    if sig is not None and sig.get(field) != value:
        sig[field] = value
        return True
    return False


def _drop_signal(signals: list[dict], indicator: str) -> bool:
    for i, s in enumerate(signals):
        if s.get("indicator") == indicator:
            signals.pop(i)
            return True
    return False


def _load(name: str) -> dict:
    return json.loads((CFG_DIR / name).read_text())


def _save(name: str, cfg: dict) -> None:
    (CFG_DIR / name).write_text(json.dumps(cfg, indent=2) + "\n")


def relax_001():
    # / Bollinger_PE_Oversold — Cardwell RSI reframe + redundant-RSI drop
    cfg = _load("strategy_001.json")
    sigs = cfg["entry_conditions"]["signals"]
    _set_signal_field(sigs, "rsi", "threshold", 35)
    _set_signal_field(sigs, "volume", "multiplier", 1.2)
    _save("strategy_001.json", cfg)


def relax_003():
    # / RSI_Deep_Value — lower fcf gate; rsi already <35
    cfg = _load("strategy_003.json")
    ff = cfg.setdefault("fundamental_filters", {})
    if ff.get("fcf_margin_min") == 0.1:
        ff["fcf_margin_min"] = 0.05
    _save("strategy_003.json", cfg)


def relax_005():
    # / Stochastic_FCF_MeanReversion — fcf 0.20 is rare, d/e 0.8 excludes most sectors
    cfg = _load("strategy_005.json")
    ff = cfg.setdefault("fundamental_filters", {})
    if ff.get("fcf_margin_min") == 0.2:
        ff["fcf_margin_min"] = 0.1
    if ff.get("debt_to_equity_max") == 0.8:
        ff["debt_to_equity_max"] = 1.5
    if ff.get("dcf_upside_min") == 0.1:
        ff["dcf_upside_min"] = 0.0
    _save("strategy_005.json", cfg)


def relax_006():
    # / ICT_Structure_Momentum — drop redundant volume clause; structure break
    # / is already directional
    cfg = _load("strategy_006.json")
    sigs = cfg["entry_conditions"]["signals"]
    _drop_signal(sigs, "volume")
    _save("strategy_006.json", cfg)


def relax_009():
    # / ADX_Trend_Rider — modern default ADX>20 (vs Wilder 25/35)
    cfg = _load("strategy_009.json")
    sigs = cfg["entry_conditions"]["signals"]
    _set_signal_field(sigs, "adx", "threshold", 22)
    _set_signal_field(sigs, "rsi", "threshold", 50)
    _save("strategy_009.json", cfg)


def relax_010():
    # / Bollinger_Squeeze_Breakout — volume multiplier 1.8x too tight
    cfg = _load("strategy_010.json")
    sigs = cfg["entry_conditions"]["signals"]
    _set_signal_field(sigs, "volume", "multiplier", 1.3)
    _save("strategy_010.json", cfg)


def relax_015():
    # / Swing_Momentum_Breakout — ADX>25→20; vol 1.5x→1.2x
    cfg = _load("strategy_015.json")
    sigs = cfg["entry_conditions"]["signals"]
    _set_signal_field(sigs, "adx", "threshold", 20)
    _set_signal_field(sigs, "volume", "multiplier", 1.2)
    _save("strategy_015.json", cfg)


def relax_018():
    # / Swing_Volume_Surge_Confirm — 3x volume is a 99th-percentile event
    cfg = _load("strategy_018.json")
    sigs = cfg["entry_conditions"]["signals"]
    _set_signal_field(sigs, "volume", "multiplier", 1.8)
    _save("strategy_018.json", cfg)


def relax_019():
    # / Swing_Gap_Fill_Fade — 3% gap is rare; RSI>70 can loosen
    cfg = _load("strategy_019.json")
    sigs = cfg["entry_conditions"]["signals"]
    _set_signal_field(sigs, "gap", "threshold_pct", 0.015)
    _set_signal_field(sigs, "rsi", "threshold", 60)
    _save("strategy_019.json", cfg)


def relax_020():
    # / Sector_Rotation_Tech_Leader — broaden regime, drop +2% XLK RS minimum
    cfg = _load("strategy_020.json")
    sigs = cfg["entry_conditions"]["signals"]
    # / regime condition currently "is" bull; make it "in" [bull, sideways]
    for s in sigs:
        if s.get("indicator") == "regime" and s.get("condition") == "is":
            s["condition"] = "in"
            s["values"] = ["bull", "sideways"]
            s.pop("value", None)
    _set_signal_field(sigs, "sector_relative_strength", "threshold", 0.0)
    _save("strategy_020.json", cfg)


def relax_022():
    # / Sector_Rotation_Energy_Oil_Trend — corr 0.6 excludes most non-pure-play
    cfg = _load("strategy_022.json")
    sigs = cfg["entry_conditions"]["signals"]
    _set_signal_field(sigs, "intermarket_correlation", "threshold", 0.3)
    ff = cfg.setdefault("fundamental_filters", {})
    if ff.get("pe_ratio_max") == 20:
        ff["pe_ratio_max"] = 35
    _save("strategy_022.json", cfg)


def relax_024():
    # / Sector_Rotation_Healthcare_Stable — add bull to regime list; relax beta/pe
    cfg = _load("strategy_024.json")
    sigs = cfg["entry_conditions"]["signals"]
    for s in sigs:
        if s.get("indicator") == "regime" and s.get("condition") == "in":
            if "bull" not in s.get("values", []):
                s["values"] = list(s["values"]) + ["bull"]
    _set_signal_field(sigs, "beta", "threshold", 1.1)
    ff = cfg.setdefault("fundamental_filters", {})
    if ff.get("pe_ratio_max") == 25:
        ff["pe_ratio_max"] = 40
    _save("strategy_024.json", cfg)


def relax_025():
    # / MeanRev_RSI30_Quality — strict_data already flipped; RSI 30→35; drop dcf floor
    cfg = _load("strategy_025.json")
    sigs = cfg["entry_conditions"]["signals"]
    _set_signal_field(sigs, "rsi", "threshold", 35)
    ff = cfg.setdefault("fundamental_filters", {})
    if ff.get("fcf_margin_min") == 0.1:
        ff["fcf_margin_min"] = 0.05
    if ff.get("dcf_upside_min") == 0.1:
        ff["dcf_upside_min"] = 0.0
    _save("strategy_025.json", cfg)


def relax_026():
    # / MeanRev_Bollinger_Capitulation — RSI<25 + vol 2.5x is the 1-per-year event
    cfg = _load("strategy_026.json")
    sigs = cfg["entry_conditions"]["signals"]
    _set_signal_field(sigs, "rsi", "threshold", 35)
    _set_signal_field(sigs, "volume", "multiplier", 1.5)
    _save("strategy_026.json", cfg)


def relax_027():
    # / MeanRev_ZScore_Pair — z<-2.0 is 2.3% tail; -1.5 is 6.7%. drop ATR clause
    cfg = _load("strategy_027.json")
    sigs = cfg["entry_conditions"]["signals"]
    _set_signal_field(sigs, "zscore_return", "threshold", -1.5)
    _drop_signal(sigs, "atr_percentile")
    _save("strategy_027.json", cfg)


def relax_028():
    # / Event_Earnings_Beat_Continuation — PEAD literature: 20-60d holding;
    # / 3-day report window is geometrically rare; drop redundant volume
    cfg = _load("strategy_028.json")
    sigs = cfg["entry_conditions"]["signals"]
    for s in sigs:
        if s.get("indicator") == "earnings_surprise" and s.get("max_days_since_report") == 3:
            s["max_days_since_report"] = 10
    _drop_signal(sigs, "volume")
    _save("strategy_028.json", cfg)


def relax_029():
    # / Event_Insider_Cluster_Buy — cluster def: 3+ in 14d OR 2+ in 30d per 2iQ;
    # / $500k hard floor excludes smaller-cap C-suite buys with alpha
    cfg = _load("strategy_029.json")
    sigs = cfg["entry_conditions"]["signals"]
    for s in sigs:
        if s.get("indicator") == "insider_cluster" and s.get("threshold") == 3:
            s["threshold"] = 2
        if s.get("indicator") == "insider_net_dollar" and s.get("threshold_usd") == 500000:
            s["threshold_usd"] = 100000
    _save("strategy_029.json", cfg)


def main():
    ops = [
        relax_001, relax_003, relax_005, relax_006, relax_009, relax_010,
        relax_015, relax_018, relax_019, relax_020, relax_022, relax_024,
        relax_025, relax_026, relax_027, relax_028, relax_029,
    ]
    for fn in ops:
        fn()
        print(f"applied {fn.__name__}")
    print(f"\n{len(ops)} strategies updated.")


if __name__ == "__main__":
    main()
