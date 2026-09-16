import asyncio

from app.database import init_db
from app.main import _run_bot_workers, _seed_admin


async def main() -> None:
    await init_db()
    await _seed_admin()
    await _run_bot_workers(asyncio.Event())


if __name__ == "__main__":
    asyncio.run(main())
