"""OpenStreetMap amenities around each covered locality, via the Overpass API.

One request per locality per category group, within RADIUS_M of the locality
centroid. Results land in `amenities`, keyed by OSM id, so overlapping
localities never duplicate a school or a metro station.

OpenStreetMap data is © OpenStreetMap contributors, ODbL. Keep the attribution
anywhere the app shows it.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

from core.ingest.scrapers.base import Request, RawPage, Scraper

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
RADIUS_M = 5000

# category -> Overpass tag filters (each filter becomes node/way/relation lines)
CATEGORIES = {
    "school": ['["amenity"="school"]'],
    "college": ['["amenity"~"^(college|university)$"]'],
    "hospital": ['["amenity"="hospital"]'],
    "clinic": ['["amenity"="clinic"]'],
    "metro_station": ['["railway"="station"]["station"~"^(subway|light_rail)$"]',
                      '["public_transport"="station"]["subway"="yes"]'],
    "railway_station": ['["railway"="station"]["train"="yes"]',
                        '["railway"="station"][!"station"]'],
    "bus_station": ['["amenity"="bus_station"]'],
    "mall": ['["shop"="mall"]'],
    "supermarket": ['["shop"="supermarket"]'],
    "park": ['["leisure"="park"]'],
    "office_park": ['["landuse"="commercial"]["name"~"(IT|Tech|SEZ|Park|Hub)",i]',
                    '["office"="it"]'],
}


def build_query(lat: float, lng: float, filters: list[str]) -> str:
    around = f"(around:{RADIUS_M},{lat},{lng})"
    lines = []
    for flt in filters:
        for kind in ("node", "way", "relation"):
            lines.append(f"  {kind}{flt}{around};")
    body = "\n".join(lines)
    return f"[out:json][timeout:90];\n(\n{body}\n);\nout center tags;"


def element_point(el: dict):
    if el.get("type") == "node":
        return el.get("lat"), el.get("lon")
    center = el.get("center") or {}
    return center.get("lat"), center.get("lon")


class OsmAmenities(Scraper):
    name = "osm"
    max_age = timedelta(days=90)   # quarterly refresh
    delay_seconds = 5              # Overpass is a shared free service; be gentle
    respect_robots = False         # it is an API, not a crawl target

    def requests(self):
        rows = self.conn.execute(
            """
            SELECT locality_id, city_id, ST_Y(centroid::geometry), ST_X(centroid::geometry)
            FROM localities WHERE is_covered ORDER BY locality_id
            """
        ).fetchall()
        for locality_id, city_id, lat, lng in rows:
            for category, filters in CATEGORIES.items():
                query = build_query(lat, lng, filters)
                yield Request(
                    url=OVERPASS_URL,
                    method="POST",
                    data={"data": query},
                    meta={"locality_id": locality_id, "city_id": city_id, "category": category},
                )

    def parse(self, page: RawPage) -> int:
        meta = page.request.meta
        if not meta:
            return 0
        payload = json.loads(page.body)
        count = 0
        for el in payload.get("elements", []):
            lat, lng = element_point(el)
            if lat is None or lng is None:
                continue
            tags = el.get("tags", {})
            self.conn.execute(
                """
                INSERT INTO amenities (osm_id, city_id, category, name, location, tags, collected_on)
                VALUES (%s, %s, %s, %s, %s::geography, %s, %s)
                ON CONFLICT (osm_id) DO UPDATE SET
                    name = EXCLUDED.name, location = EXCLUDED.location,
                    tags = EXCLUDED.tags, collected_on = EXCLUDED.collected_on
                """,
                [f"{el['type']}/{el['id']}", meta["city_id"], meta["category"],
                 tags.get("name:en") or tags.get("name"),
                 f"SRID=4326;POINT({lng} {lat})", json.dumps(tags), date.today()],
            )
            count += 1
        return count
