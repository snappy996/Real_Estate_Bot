"""Sources whose page structure must be inspected before a parser is written.

These are registered so the CLI lists them, but they stop with a clear
message instead of guessing at HTML that hasn't been looked at. Writing a
parser against unseen markup produces confident-looking wrong data, which
is worse than no data.

To finish one:
  1. Open the portal in a browser, find a project detail page, and save it
     (Ctrl+S) into data/samples/<source>/.
  2. Share the saved page; the parser is written and tested against it.
  3. If the portal's search sits behind a CAPTCHA, the scraper only fetches
     detail pages you list by URL in data/manual/<source>_urls.txt; the
     CAPTCHA itself is never bypassed.

Until then, add projects and rates by hand in data/manual/ (see DATA_FORMAT.md).
"""

from __future__ import annotations

from core.ingest.scrapers.base import Scraper


class _NotReady(Scraper):
    portal = ""
    covers = ""

    def requests(self):
        raise SystemExit(
            f"The {self.name} scraper needs a sample page first ({self.covers}).\n"
            f"Portal: {self.portal}\n"
            "See core/ingest/scrapers/manual_sources.py for the three steps. "
            "Meanwhile, enter data in data/manual/ using DATA_FORMAT.md."
        )

    def parse(self, page):
        return 0


class TsRera(_NotReady):
    name = "ts_rera"
    portal = "https://rera.telangana.gov.in"
    covers = "Hyderabad projects, builders, possession dates, status"


class MahaRera(_NotReady):
    name = "maharera"
    portal = "https://maharera.maharashtra.gov.in"
    covers = "Pune projects, builders, possession dates, status"


class TelanganaGuidelineValues(_NotReady):
    name = "ts_guideline_values"
    portal = "https://registration.telangana.gov.in"
    covers = "government market values per locality, Hyderabad"


class MaharashtraReadyReckoner(_NotReady):
    name = "mh_ready_reckoner"
    portal = "https://igrmaharashtra.gov.in"
    covers = "ready reckoner rates per zone, Pune"
