#!/usr/bin/env python3
"""
One-time (re-runnable) backfill: geocode every restaurant row that has a
non-blank `address` but no latitude/longitude yet, and fill in
latitude/longitude/city/state via services/geocoding.py (Google Maps
Geocoding API). Needed so existing restaurants (created before the location
feature shipped) show up in radius-based marketplace browsing and
menu-comparison scoping, not just name search.

Connects directly to the backend's Postgres via DATABASE_URL -- same
connection string the backend itself uses (backend/database/connection.py).
Safe to re-run: it only ever selects rows still missing coordinates, so an
earlier partial run (e.g. interrupted mid-way, or some addresses failing to
geocode) just gets retried on the next run.

Usage:
    export DATABASE_URL="postgresql://docintel:docintel_secret@localhost:5432/docintel"
    export GOOGLE_MAPS_API_KEY="..."
    python3 scripts/backfill_restaurant_locations.py [--limit N] [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from services import geocoding  # noqa: E402

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://docintel:docintel_secret@localhost:5432/docintel")


async def main(limit: int, dry_run: bool) -> None:
    if not geocoding.configured():
        print("ERROR: no Google Maps API key configured (GOOGLE_MAPS_API_KEY / GOOGLE_API_KEY). Aborting.", file=sys.stderr)
        sys.exit(1)

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        rows = await conn.fetch(
            """
            SELECT id, name, address FROM restaurants
            WHERE TRIM(address) <> '' AND latitude IS NULL
            ORDER BY created_at
            LIMIT $1
            """,
            limit,
        )
        print(f"Found {len(rows)} restaurant(s) with an address but no coordinates.")
        geocoded = 0
        failed = 0
        for row in rows:
            address = row["address"]
            location = await geocoding.geocode_address(address)
            if not location:
                failed += 1
                print(f"  [FAIL] {row['name']!r} ({address!r}) -- could not geocode")
                continue
            geocoded += 1
            print(
                f"  [OK]   {row['name']!r} -> lat={location['latitude']}, lng={location['longitude']}, "
                f"city={location['city']!r}, state={location['state']!r}"
            )
            if not dry_run:
                await conn.execute(
                    "UPDATE restaurants SET latitude=$2, longitude=$3, city=$4, state=$5 WHERE id=$1",
                    row["id"], location["latitude"], location["longitude"], location["city"], location["state"],
                )
        print(f"\nDone. geocoded={geocoded} failed={failed} dry_run={dry_run}")
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=1000, help="Max rows to process in this run")
    parser.add_argument("--dry-run", action="store_true", help="Geocode and print, but don't write to the DB")
    args = parser.parse_args()
    asyncio.run(main(args.limit, args.dry_run))
