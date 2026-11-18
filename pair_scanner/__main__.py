import asyncio
import os

import sentry_sdk
from dotenv import load_dotenv

from pair_scanner import start_scanner_bot, stop_scanner_bot


async def main():
    load_dotenv()
    sentry_sdk.init(dsn=os.environ.get("SCANNER_SENTRY_DSN"))
    try:
        await start_scanner_bot()
    except ValueError as e:
        print(f"❌ {e}. Add them to .env (see .env.example).")
        return
    try:
        await asyncio.Event().wait()
    finally:
        await stop_scanner_bot()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
