import asyncio, asyncpg

async def run():
    conn = await asyncpg.connect("postgresql://neondb_owner:npg_xFszcm0tCNO5@ep-muddy-cake-and2da36-pooler.c-6.us-east-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require")
    with open("src/data/migrations/012_intraday_bars.sql") as f:
        await conn.execute(f.read())
    await conn.close()
    print("migration applied")

asyncio.run(run())
