import asyncio
from src.data.db import init_db, close_db
from src.data.sec_filings import fetch_insider_trades, store_insider_trades
from src.data.symbols import FULL_UNIVERSE, is_crypto

async def run():
    pool = await init_db()
    for s in [x for x in FULL_UNIVERSE if not is_crypto(x)]:
        try:
            trades = await fetch_insider_trades(s)
            if trades:
                await store_insider_trades(pool, trades)
            print(f"{s}: {len(trades) if trades else 0} trades")
        except Exception as e:
            print(f"{s}: {e}")
    await close_db()

asyncio.run(run())
