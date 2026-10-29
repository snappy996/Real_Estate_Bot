"""Load seed reference data and hand-filled CSVs into Neon.

    python -m core.ingest.load seeds     # cities, localities, aliases
    python -m core.ingest.load manual    # data/manual/*.csv (validated first)
    python -m core.ingest.load all

Every write is an upsert on a natural key, so running it twice never
duplicates rows; editing a CSV row and re-running updates it in place.
"""

from __future__ import annotations

import csv
import re
import sys

from core.db import transaction
from core.ingest.validate import MANUAL_DIR, SEED_DIR, RowChecker, load_cities, \
    load_locality_lookup, validate_file


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _point(lat, lng):
    return None if lat is None or lng is None else f"SRID=4326;POINT({lng} {lat})"


# ---------------------------------------------------------------------------
# Seeds
# ---------------------------------------------------------------------------

def load_seeds(conn) -> dict[str, int]:
    counts = {}
    with open(SEED_DIR / "cities.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        conn.execute(
            """
            INSERT INTO cities (city_id, name, state, rera_portal_url, stamp_duty_pct,
                                registration_pct, registration_cap_inr, other_duty_pct,
                                rates_source, rates_verified_on)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (city_id) DO UPDATE SET
                name = EXCLUDED.name, state = EXCLUDED.state,
                rera_portal_url = EXCLUDED.rera_portal_url,
                stamp_duty_pct = EXCLUDED.stamp_duty_pct,
                registration_pct = EXCLUDED.registration_pct,
                registration_cap_inr = EXCLUDED.registration_cap_inr,
                other_duty_pct = EXCLUDED.other_duty_pct,
                rates_source = EXCLUDED.rates_source,
                rates_verified_on = EXCLUDED.rates_verified_on
            """,
            [r["city_id"], r["name"], r["state"], r["rera_portal_url"] or None,
             r["stamp_duty_pct"] or None, r["registration_pct"] or None,
             r["registration_cap_inr"] or None, r["other_duty_pct"] or None,
             r["rates_source"] or None, r["rates_verified_on"] or None],
        )
    counts["cities"] = len(rows)

    with open(SEED_DIR / "localities.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        conn.execute(
            """
            INSERT INTO localities (locality_id, city_id, name, zone, centroid,
                                    is_covered, centroid_source)
            VALUES (%s,%s,%s,%s,%s::geography,%s,%s)
            ON CONFLICT (locality_id) DO UPDATE SET
                name = EXCLUDED.name, zone = EXCLUDED.zone,
                centroid = COALESCE(
                    CASE WHEN localities.centroid_source = 'approximate'
                         THEN EXCLUDED.centroid END, localities.centroid),
                is_covered = EXCLUDED.is_covered
            """,
            [r["locality_id"], r["city_id"], r["name"], r["zone"] or None,
             _point(float(r["lat"]), float(r["lng"])),
             r["is_covered"].lower() == "true", r["centroid_source"] or None],
        )
    counts["localities"] = len(rows)

    with open(SEED_DIR / "locality_aliases.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        conn.execute(
            "INSERT INTO locality_aliases (alias, locality_id) VALUES (%s,%s) "
            "ON CONFLICT DO NOTHING",
            [r["alias"], r["locality_id"]],
        )
    counts["aliases"] = len(rows)
    return counts


# ---------------------------------------------------------------------------
# Manual CSVs
# ---------------------------------------------------------------------------

def _prov(r):
    return [r["source"], r["source_url"], r["collected_on"], "manual", r["notes"]]


def upsert_prices(conn, rows):
    for r in rows:
        conn.execute(
            """
            INSERT INTO price_history (locality_id, period, property_type, price_basis,
                psf_low, psf_median, psf_high, area_basis, sample_size,
                source, source_url, collected_on, collected_by, notes)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (locality_id, period, property_type, price_basis, area_basis, source)
            DO UPDATE SET psf_low = EXCLUDED.psf_low, psf_median = EXCLUDED.psf_median,
                psf_high = EXCLUDED.psf_high, sample_size = EXCLUDED.sample_size,
                source_url = EXCLUDED.source_url, collected_on = EXCLUDED.collected_on,
                notes = EXCLUDED.notes
            """,
            [r["locality_id"], r["period"], r["property_type"], r["price_basis"],
             r["psf_low"], r["psf_median"], r["psf_high"], r["area_basis"],
             r["sample_size"], *_prov(r)],
        )


def upsert_rents(conn, rows):
    for r in rows:
        conn.execute(
            """
            INSERT INTO rent_history (locality_id, period, bhk, furnishing,
                rent_low, rent_median, rent_high, typical_sqft, sample_size,
                source, source_url, collected_on, collected_by, notes)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (locality_id, period, bhk, furnishing, source)
            DO UPDATE SET rent_low = EXCLUDED.rent_low, rent_median = EXCLUDED.rent_median,
                rent_high = EXCLUDED.rent_high, typical_sqft = EXCLUDED.typical_sqft,
                sample_size = EXCLUDED.sample_size, source_url = EXCLUDED.source_url,
                collected_on = EXCLUDED.collected_on, notes = EXCLUDED.notes
            """,
            [r["locality_id"], r["period"], r["bhk"], r["furnishing"],
             r["rent_low"], r["rent_median"], r["rent_high"], r["typical_sqft"],
             r["sample_size"], *_prov(r)],
        )


def upsert_projects(conn, rows):
    for r in rows:
        builder_id = None
        if r["builder"]:
            builder_id = slugify(r["builder"])
            conn.execute(
                "INSERT INTO builders (builder_id, name, source, collected_on) "
                "VALUES (%s,%s,%s,%s) ON CONFLICT (builder_id) DO NOTHING",
                [builder_id, r["builder"], r["source"], r["collected_on"]],
            )
        conn.execute(
            """
            INSERT INTO projects (rera_id, city_id, locality_id, name, builder_id, location,
                project_type, unit_types, total_units, psf_min, psf_max, launch_date,
                possession_date, status, source, source_url, collected_on, collected_by, notes)
            VALUES (%s,%s,%s,%s,%s,%s::geography,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (rera_id) DO UPDATE SET
                locality_id = COALESCE(EXCLUDED.locality_id, projects.locality_id),
                name = EXCLUDED.name,
                builder_id = COALESCE(EXCLUDED.builder_id, projects.builder_id),
                location = COALESCE(EXCLUDED.location, projects.location),
                project_type = EXCLUDED.project_type,
                unit_types = COALESCE(EXCLUDED.unit_types, projects.unit_types),
                total_units = COALESCE(EXCLUDED.total_units, projects.total_units),
                psf_min = COALESCE(EXCLUDED.psf_min, projects.psf_min),
                psf_max = COALESCE(EXCLUDED.psf_max, projects.psf_max),
                launch_date = COALESCE(EXCLUDED.launch_date, projects.launch_date),
                possession_date = COALESCE(EXCLUDED.possession_date, projects.possession_date),
                status = EXCLUDED.status, source_url = EXCLUDED.source_url,
                collected_on = EXCLUDED.collected_on, notes = EXCLUDED.notes
            """,
            [r["rera_id"], r["city_id"], r["locality_id"], r["name"], builder_id,
             _point(r["lat"], r["lng"]), r["project_type"], r["unit_types"],
             r["total_units"], r["psf_min"], r["psf_max"], r["launch_date"],
             r["possession_date"], r["status"], *_prov(r)],
        )


def upsert_infra(conn, rows):
    for r in rows:
        conn.execute(
            """
            INSERT INTO infra_events (city_id, locality_id, event_type, name, status,
                announced_on, started_on, expected_on, completed_on, location,
                source, source_url, collected_on, collected_by, notes)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::geography,%s,%s,%s,%s,%s)
            ON CONFLICT (city_id, name, status) DO UPDATE SET
                locality_id = COALESCE(EXCLUDED.locality_id, infra_events.locality_id),
                event_type = EXCLUDED.event_type,
                announced_on = COALESCE(EXCLUDED.announced_on, infra_events.announced_on),
                started_on = COALESCE(EXCLUDED.started_on, infra_events.started_on),
                expected_on = COALESCE(EXCLUDED.expected_on, infra_events.expected_on),
                completed_on = COALESCE(EXCLUDED.completed_on, infra_events.completed_on),
                location = COALESCE(EXCLUDED.location, infra_events.location),
                source_url = EXCLUDED.source_url, collected_on = EXCLUDED.collected_on,
                notes = EXCLUDED.notes
            """,
            [r["city_id"], r["locality_id"], r["event_type"], r["name"], r["status"],
             r["announced_on"], r["started_on"], r["expected_on"], r["completed_on"],
             _point(r["lat"], r["lng"]), *_prov(r)],
        )


UPSERTS = {
    "prices.csv": upsert_prices,
    "rents.csv": upsert_rents,
    "projects.csv": upsert_projects,
    "infra_events.csv": upsert_infra,
}


def load_manual(conn) -> dict[str, int]:
    checker = RowChecker(load_locality_lookup(), load_cities())
    counts, problems = {}, []
    results = {}
    for name in UPSERTS:
        path = MANUAL_DIR / name
        if path.exists():
            results[name] = validate_file(path, checker)
            problems.extend(results[name].errors)
    if problems:
        raise SystemExit("Fix these before loading (nothing was written):\n  "
                         + "\n  ".join(problems))
    for name, res in results.items():
        UPSERTS[name](conn, res.rows)
        counts[name] = len(res.rows)
    return counts


def main(argv):
    what = argv[0] if argv else "all"
    with transaction() as conn:
        if what in ("seeds", "all"):
            print("seeds:", load_seeds(conn))
        if what in ("manual", "all"):
            print("manual:", load_manual(conn))


if __name__ == "__main__":
    main(sys.argv[1:])
