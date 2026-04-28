# / decimal/date/datetime serialization for json responses

from __future__ import annotations


def serialize_one(row: dict | None) -> dict | None:
    if row is None:
        return None
    result = {}
    for k, v in row.items():
        if hasattr(v, "isoformat"):
            result[k] = v.isoformat()
        elif isinstance(v, (int, float, str, bool, type(None), dict, list)):
            result[k] = v
        else:
            result[k] = str(v)
    return result


def serialize(rows: list[dict]) -> list[dict]:
    out: list[dict] = []
    for r in rows:
        s = serialize_one(r)
        if s is not None:
            out.append(s)
    return out


def serialize_position(p) -> dict:
    return {
        "symbol": p.symbol,
        "side": p.side,
        "qty": p.qty,
        "market_value": p.market_value,
        "entry_price": p.avg_entry_price,
        "unrealized_pl": p.unrealized_pnl,
        "current_price": p.current_price,
    }
