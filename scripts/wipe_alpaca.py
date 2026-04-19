#!/usr/bin/env python3
# / wipe alpaca paper account: cancel all open orders + close all positions at market
# / uses alpaca's bulk endpoints: DELETE /v2/orders, DELETE /v2/positions
# / usage: python -m scripts.wipe_alpaca --yes

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

import structlog

from src.data.alpaca_client import alpaca_base_url, alpaca_headers, get_alpaca_client

logger = structlog.get_logger(__name__)


async def _list_open_orders() -> list[dict]:
    client = await get_alpaca_client()
    resp = await client.get(
        f"{alpaca_base_url()}/v2/orders?status=open&limit=500",
        headers=alpaca_headers(),
        timeout=15.0,
    )
    resp.raise_for_status()
    return resp.json()


async def _list_positions() -> list[dict]:
    client = await get_alpaca_client()
    resp = await client.get(
        f"{alpaca_base_url()}/v2/positions",
        headers=alpaca_headers(),
        timeout=15.0,
    )
    resp.raise_for_status()
    return resp.json()


async def _cancel_all_orders() -> int:
    # / DELETE /v2/orders cancels every open order, returns per-order status array
    client = await get_alpaca_client()
    resp = await client.delete(
        f"{alpaca_base_url()}/v2/orders",
        headers=alpaca_headers(),
        timeout=30.0,
    )
    if resp.status_code not in (200, 207, 204):
        logger.error("cancel_all_failed", status=resp.status_code, body=resp.text[:500])
        resp.raise_for_status()
    data = resp.json() if resp.text else []
    return len(data) if isinstance(data, list) else 0


async def _close_all_positions() -> int:
    # / DELETE /v2/positions?cancel_orders=true liquidates all positions at market
    client = await get_alpaca_client()
    resp = await client.delete(
        f"{alpaca_base_url()}/v2/positions?cancel_orders=true",
        headers=alpaca_headers(),
        timeout=30.0,
    )
    if resp.status_code not in (200, 207, 204):
        logger.error("close_all_failed", status=resp.status_code, body=resp.text[:500])
        resp.raise_for_status()
    data = resp.json() if resp.text else []
    return len(data) if isinstance(data, list) else 0


async def wipe(dry_run: bool) -> None:
    base = alpaca_base_url()
    logger.info("wipe_alpaca_start", endpoint=base, dry_run=dry_run)

    if "paper" not in base:
        logger.warning("not_paper_endpoint", endpoint=base)
        print(f"WARNING: endpoint is {base} — this looks like a LIVE account.")
        print("refusing to proceed. set ALPACA_BASE_URL to paper endpoint first.")
        sys.exit(2)

    positions = await _list_positions()
    orders = await _list_open_orders()

    print(f"current state:")
    print(f"  positions: {len(positions)}")
    for p in positions:
        print(f"    {p['symbol']}: qty={p['qty']} value=${float(p['market_value']):.2f} pnl=${float(p['unrealized_pl']):.2f}")
    print(f"  open orders: {len(orders)}")
    for o in orders:
        print(f"    {o['symbol']} {o['side']} {o.get('qty') or o.get('notional')} ({o['type']}) status={o['status']}")

    if dry_run:
        print("\ndry-run: nothing changed. rerun with --yes to execute.")
        return

    print("\nexecuting wipe...")

    n_cancelled = await _cancel_all_orders()
    logger.info("orders_cancelled", count=n_cancelled)
    print(f"  cancelled {n_cancelled} open orders")

    n_closed = await _close_all_positions()
    logger.info("positions_closed", count=n_closed)
    print(f"  submitted close orders for {n_closed} positions (market, liquidate at open)")

    print("\nwait 30-60s for fills, then re-run with --dry-run to verify 0 positions / 0 orders.")


def main() -> None:
    parser = argparse.ArgumentParser(description="wipe alpaca paper account: cancel orders + close positions")
    parser.add_argument("--yes", action="store_true", help="actually execute the wipe (default is dry-run)")
    args = parser.parse_args()

    asyncio.run(wipe(dry_run=not args.yes))


if __name__ == "__main__":
    main()
