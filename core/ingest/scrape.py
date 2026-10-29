"""Run scrapers.

    python -m core.ingest.scrape --list
    python -m core.ingest.scrape osm              # fetch what is stale, parse what changed
    python -m core.ingest.scrape osm --force      # re-fetch everything now
    python -m core.ingest.scrape osm --reparse    # re-parse stored pages, no network
"""

from __future__ import annotations

import argparse

from core.db import connect
from core.ingest.scrapers.manual_sources import (MaharashtraReadyReckoner, MahaRera,
                                                 TelanganaGuidelineValues, TsRera)
from core.ingest.scrapers.osm import OsmAmenities

SCRAPERS = {cls.name: cls for cls in (
    OsmAmenities, TsRera, MahaRera, TelanganaGuidelineValues, MaharashtraReadyReckoner,
)}
READY = {"osm"}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("source", nargs="?", choices=sorted(SCRAPERS))
    ap.add_argument("--list", action="store_true", help="list scrapers and their status")
    ap.add_argument("--force", action="store_true", help="ignore cached copies and re-fetch")
    ap.add_argument("--reparse", action="store_true", help="parse stored pages only, no network")
    args = ap.parse_args()

    if args.list or not args.source:
        for name, cls in sorted(SCRAPERS.items()):
            status = "ready" if name in READY else "needs a sample page"
            print(f"  {name:<22} {status}")
        return

    conn = connect()
    try:
        scraper = SCRAPERS[args.source](conn, force=args.force)
        stats = scraper.reparse() if args.reparse else scraper.run()
        print(f"{args.source}: {stats}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
