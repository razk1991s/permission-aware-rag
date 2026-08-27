#!/usr/bin/env python3
"""מריץ את המיגרציות שטרם הורצו."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.db import dispose_engine, run_migrations


async def main() -> None:
    applied = await run_migrations()
    print("הורצו:", ", ".join(applied) if applied else "אין מיגרציות חדשות")
    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
