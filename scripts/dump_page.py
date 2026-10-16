"""Fetch one official page through the SafePageFetcher and print its chunks (for curation / debugging).

Usage: python scripts/dump_page.py <url> [--max-chars N]
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings  # noqa: E402
from app.tools.fetcher import SafePageFetcher  # noqa: E402


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--max-chars", type=int, default=60000)
    a = ap.parse_args()
    f = SafePageFetcher(Settings(_env_file=None))
    page = await f.fetch(a.url)
    s = page.source
    print(f"STATUS={s.fetch_status} CATEGORY={s.error_category} FINAL_URL={s.final_url}")
    print(f"TITLE={s.title}\nSOURCE_ID={s.source_id} CHUNKS={len(page.chunks)}")
    out = 0
    for c in page.chunks:
        block = f"\n[{c.chunk_id}] ## {c.heading or ''}\n{c.text}\n"
        out += len(block)
        if out > a.max_chars:
            print("\n...TRUNCATED...")
            break
        print(block)
    print("\nLINKS:")
    for link in page.links[:80]:
        print(f"- {link.text[:60]} | {link.url}")
    await f.aclose()


if __name__ == "__main__":
    asyncio.run(main())
