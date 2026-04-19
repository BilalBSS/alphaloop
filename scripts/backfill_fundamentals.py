import asyncio
import os
from src.data.fundamentals import fetch_all_fundamentals, store_fundamentals
from src.data.db import init_db, close_db
from src.data.symbols import EQUITY_UNIVERSE

async def run():
    pool = await init_db()
    data = await fetch_all_fundamentals(EQUITY_UNIVERSE)
    count = await store_fundamentals(pool, data)
    print(f"stored {count} symbols")
    for d in data:
        src = d.get("data_source", "?")
        shares = d.get("shares_outstanding")
        nd = d.get("net_debt")
        print(f"  {d['symbol']}: source={src}, shares={shares}, net_debt={nd}")
    await close_db()

asyncio.run(run())
