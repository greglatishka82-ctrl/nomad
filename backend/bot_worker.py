import argparse
import asyncio

from app.database import init_db
from app.main import _run_bot_workers, _seed_admin


async def main(client_bot_polling_enabled: bool = True) -> None:
    await init_db()
    await _seed_admin()
    await _run_bot_workers(
        asyncio.Event(), client_bot_polling_enabled=client_bot_polling_enabled
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--disable-client-bot",
        action="store_true",
        help="Disable client booking bot polling while keeping instructor/report bots and scheduled tasks active",
    )
    args = parser.parse_args()
    asyncio.run(main(client_bot_polling_enabled=not args.disable_client_bot))
