"""Validate the hand-filled CSVs in data/manual/ before they touch the database.

Pure standard library, so you can run it with no database and no API keys:

    python -m core.ingest.validate            # checks every file in data/manual/
    python -m core.ingest.validate prices.csv # checks one file

Each row is checked for required columns, types, allowed values and sane
ranges, and the locality name is resolved through the seed aliases, so
"Kokapeta" or "KPHB" are accepted and mapped to the right locality.
"""

from __future__ import annotations

import csv
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SEED_DIR = ROOT / "data" / "seed"
MANUAL_DIR = ROOT / "data" / "manual"

PROPERTY_TYPES = {"apartment", "villa", "plot", "independent_house"}
PRICE_BASES = {"asking", "transaction", "guideline", "ready_reckoner", "index"}
AREA_BASES = {"super_built_up", "built_up", "carpet", "plot"}
FURNISHING = {"unfurnished", "semi", "full", "unknown"}
PROJECT_TYPES = {"apartment", "villa", "plot", "mixed", "commercial"}
PROJECT_STATUS = {"pre_launch", "under_construction", "ready", "completed", "lapsed", "unknown"}
EVENT_TYPES = {"metro", "road", "flyover", "it_park", "airport", "rail",
               "hospital", "school", "mall", "zoning", "other"}
EVENT_STATUS = {"announced", "approved", "under_construction", "operational", "cancelled"}

# Sanity ranges. Wide on purpose: they catch typos (an extra zero, rent typed
# as yearly), not unusual-but-real values.
PSF_RANGE = (500, 100_000)          # INR per sq ft
RENT_RANGE = (3_000, 1_000_000)     # INR per month
CITY_BOUNDS = {                     # lat_min, lat_max, lng_min, lng_max
    "hyd": (17.0, 17.9, 78.0, 78.9),
    "pune": (18.2, 18.9, 73.5, 74.3),
}


@dataclass
class Result:
    rows: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Locality resolution from the seed files
# ---------------------------------------------------------------------------

def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def load_locality_lookup(seed_dir: Path = SEED_DIR) -> dict[tuple[str, str], str]:
    """Map (city_id, normalised name) -> locality_id, using names, ids and aliases."""
    lookup: dict[tuple[str, str], str] = {}
    city_of: dict[str, str] = {}
    with open(seed_dir / "localities.csv", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            lid, city = row["locality_id"], row["city_id"]
            city_of[lid] = city
            lookup[(city, _norm(row["name"]))] = lid
            lookup[(city, _norm(lid))] = lid
            lookup[(city, _norm(lid.split("-", 1)[1]))] = lid
    aliases = seed_dir / "locality_aliases.csv"
    if aliases.exists():
        with open(aliases, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                lid = row["locality_id"]
                if lid in city_of:
                    lookup[(city_of[lid], _norm(row["alias"]))] = lid
    return lookup


def load_cities(seed_dir: Path = SEED_DIR) -> dict[str, str]:
    """Map normalised city name or id -> city_id."""
    out: dict[str, str] = {}
    with open(seed_dir / "cities.csv", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            out[_norm(row["city_id"])] = row["city_id"]
            out[_norm(row["name"])] = row["city_id"]
    out.setdefault("hyderabad", "hyd")
    return out


# ---------------------------------------------------------------------------
# Field parsers: each returns (value, error_message_or_None)
# ---------------------------------------------------------------------------

def _num(raw: str, label: str):
    cleaned = raw.replace(",", "").replace("₹", "").strip()
    if cleaned == "":
        return None, None
    try:
        return float(cleaned), None
    except ValueError:
        return None, f"{label} is not a number: {raw!r}"


def _int(raw: str, label: str):
    value, err = _num(raw, label)
    if err or value is None:
        return value, err
    if value != int(value):
        return None, f"{label} must be a whole number: {raw!r}"
    return int(value), None


def _date(raw: str, label: str):
    raw = raw.strip()
    if raw == "":
        return None, None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date(), None
    except ValueError:
        return None, f"{label} must be YYYY-MM-DD: {raw!r}"


def _period(raw: str, label: str = "period"):
    raw = raw.strip()
    try:
        return datetime.strptime(raw, "%Y-%m").date().replace(day=1), None
    except ValueError:
        return None, f"{label} must be YYYY-MM (e.g. 2026-10): {raw!r}"


def _choice(raw: str, allowed: set[str], label: str, default: str | None = None):
    value = raw.strip().lower().replace(" ", "_").replace("-", "_")
    if value == "" and default is not None:
        return default, None
    if value not in allowed:
        return None, f"{label} must be one of {sorted(allowed)}: {raw!r}"
    return value, None


# ---------------------------------------------------------------------------
# Row validators
# ---------------------------------------------------------------------------

class RowChecker:
    def __init__(self, lookup, cities, today: date | None = None):
        self.lookup = lookup
        self.cities = cities
        self.today = today or date.today()

    def city_and_locality(self, row, errs, locality_required=True):
        city = self.cities.get(_norm(row.get("city", "")))
        if city is None:
            errs.append(f"unknown city {row.get('city')!r} (use Hyderabad or Pune)")
            return None, None
        raw_loc = row.get("locality", "").strip()
        if raw_loc == "":
            if locality_required:
                errs.append("locality is required")
            return city, None
        lid = self.lookup.get((city, _norm(raw_loc)))
        if lid is None:
            errs.append(f"unknown locality {raw_loc!r} in {city}; "
                        "add it to data/seed/locality_aliases.csv if it is a spelling variant")
        return city, lid

    def provenance(self, row, out, errs, warns):
        source = row.get("source", "").strip()
        if not source:
            errs.append("source is required (website or document name)")
        out["source"] = source
        out["source_url"] = row.get("source_url", "").strip() or None
        collected, err = _date(row.get("collected_on", ""), "collected_on")
        if err:
            errs.append(err)
        elif collected is None:
            errs.append("collected_on is required (the day you looked it up)")
        elif collected > self.today:
            errs.append(f"collected_on {collected} is in the future")
        out["collected_on"] = collected
        out["notes"] = row.get("notes", "").strip() or None
        if out["source_url"] and not out["source_url"].startswith(("http://", "https://")):
            warns.append("source_url does not start with http(s)://")

    def low_mid_high(self, row, keys, label, rng, out, errs, warns):
        vals = []
        for key in keys:
            v, err = _num(row.get(key, ""), key)
            if err:
                errs.append(err)
            out[key] = v
            vals.append(v)
        present = [v for v in vals if v is not None]
        if not present:
            errs.append(f"at least one of {', '.join(keys)} is required")
            return
        lo, hi = rng
        for key, v in zip(keys, vals):
            if v is not None and not lo <= v <= hi:
                errs.append(f"{key}={v:,.0f} is outside the expected {label} range "
                            f"{lo:,}–{hi:,}; check for a typo")
        low, mid, high = vals
        if low is not None and high is not None and low > high:
            errs.append(f"{keys[0]} is greater than {keys[2]}")
        if mid is not None and low is not None and mid < low:
            errs.append(f"{keys[1]} is below {keys[0]}")
        if mid is not None and high is not None and mid > high:
            errs.append(f"{keys[1]} is above {keys[2]}")

    def point(self, row, city, out, errs):
        lat, e1 = _num(row.get("lat", ""), "lat")
        lng, e2 = _num(row.get("lng", ""), "lng")
        errs.extend(e for e in (e1, e2) if e)
        if (lat is None) != (lng is None):
            errs.append("give both lat and lng, or neither")
        elif lat is not None and city in CITY_BOUNDS:
            la0, la1, ln0, ln1 = CITY_BOUNDS[city]
            if not (la0 <= lat <= la1 and ln0 <= lng <= ln1):
                errs.append(f"lat/lng {lat},{lng} is outside {city}; "
                            "check the order (lat first) and decimals")
        out["lat"], out["lng"] = lat, lng

    # -- per file ------------------------------------------------------------

    def prices(self, row):
        out, errs, warns = {}, [], []
        city, lid = self.city_and_locality(row, errs)
        out["city_id"], out["locality_id"] = city, lid
        out["period"], e = _period(row.get("period", ""))
        if e:
            errs.append(e)
        out["property_type"], e = _choice(row.get("property_type", ""), PROPERTY_TYPES,
                                          "property_type", default="apartment")
        if e:
            errs.append(e)
        out["price_basis"], e = _choice(row.get("price_basis", ""), PRICE_BASES, "price_basis")
        if e:
            errs.append(e)
        out["area_basis"], e = _choice(row.get("area_basis", ""), AREA_BASES, "area_basis",
                                       default="super_built_up")
        if e:
            errs.append(e)
        self.low_mid_high(row, ("psf_low", "psf_median", "psf_high"), "price per sq ft",
                          PSF_RANGE, out, errs, warns)
        out["sample_size"], e = _int(row.get("sample_size", ""), "sample_size")
        if e:
            errs.append(e)
        self.provenance(row, out, errs, warns)
        return out, errs, warns

    def rents(self, row):
        out, errs, warns = {}, [], []
        city, lid = self.city_and_locality(row, errs)
        out["city_id"], out["locality_id"] = city, lid
        out["period"], e = _period(row.get("period", ""))
        if e:
            errs.append(e)
        bhk, e = _num(row.get("bhk", "").upper().replace("BHK", ""), "bhk")
        if e:
            errs.append(e)
        elif bhk is None:
            errs.append("bhk is required (1, 2, 2.5, 3 ...)")
        elif not 0.5 <= bhk <= 10:
            errs.append(f"bhk={bhk} looks wrong")
        out["bhk"] = bhk
        out["furnishing"], e = _choice(row.get("furnishing", ""), FURNISHING, "furnishing",
                                       default="unknown")
        if e:
            errs.append(e)
        self.low_mid_high(row, ("rent_low", "rent_median", "rent_high"), "monthly rent",
                          RENT_RANGE, out, errs, warns)
        out["typical_sqft"], e = _int(row.get("typical_sqft", ""), "typical_sqft")
        if e:
            errs.append(e)
        out["sample_size"], e = _int(row.get("sample_size", ""), "sample_size")
        if e:
            errs.append(e)
        self.provenance(row, out, errs, warns)
        return out, errs, warns

    def projects(self, row):
        out, errs, warns = {}, [], []
        rera = row.get("rera_id", "").strip().upper()
        if not rera:
            errs.append("rera_id is required (copy it exactly from the RERA certificate)")
        elif not re.fullmatch(r"[A-Z0-9/\-]{6,40}", rera):
            errs.append(f"rera_id {rera!r} has unexpected characters")
        out["rera_id"] = rera
        city, lid = self.city_and_locality(row, errs, locality_required=False)
        out["city_id"], out["locality_id"] = city, lid
        if city == "pune" and rera and not rera.startswith("P5"):
            warns.append("MahaRERA project IDs for Pune usually start with P52; double-check")
        if city == "hyd" and rera and not rera.startswith("P02"):
            warns.append("TS RERA project IDs usually start with P02; double-check")
        out["name"] = row.get("name", "").strip()
        if not out["name"]:
            errs.append("name is required")
        out["builder"] = row.get("builder", "").strip() or None
        self.point(row, city, out, errs)
        out["project_type"], e = _choice(row.get("project_type", ""), PROJECT_TYPES,
                                         "project_type", default="apartment")
        if e:
            errs.append(e)
        units = row.get("unit_types", "").upper().replace(" ", "")
        out["unit_types"] = units or None
        out["total_units"], e = _int(row.get("total_units", ""), "total_units")
        if e:
            errs.append(e)
        for key in ("psf_min", "psf_max"):
            out[key], e = _num(row.get(key, ""), key)
            if e:
                errs.append(e)
            elif out[key] is not None and not PSF_RANGE[0] <= out[key] <= PSF_RANGE[1]:
                errs.append(f"{key}={out[key]:,.0f} is outside {PSF_RANGE[0]:,}–{PSF_RANGE[1]:,}")
        if out["psf_min"] and out["psf_max"] and out["psf_min"] > out["psf_max"]:
            errs.append("psf_min is greater than psf_max")
        for key in ("launch_date", "possession_date"):
            out[key], e = _date(row.get(key, ""), key)
            if e:
                errs.append(e)
        if out["launch_date"] and out["possession_date"] and out["launch_date"] > out["possession_date"]:
            errs.append("launch_date is after possession_date")
        out["status"], e = _choice(row.get("status", ""), PROJECT_STATUS, "status",
                                   default="unknown")
        if e:
            errs.append(e)
        self.provenance(row, out, errs, warns)
        return out, errs, warns

    def infra_events(self, row):
        out, errs, warns = {}, [], []
        city, lid = self.city_and_locality(row, errs, locality_required=False)
        out["city_id"], out["locality_id"] = city, lid
        out["event_type"], e = _choice(row.get("event_type", ""), EVENT_TYPES, "event_type")
        if e:
            errs.append(e)
        out["name"] = row.get("name", "").strip()
        if not out["name"]:
            errs.append("name is required (e.g. 'Kokapet metro station')")
        out["status"], e = _choice(row.get("status", ""), EVENT_STATUS, "status")
        if e:
            errs.append(e)
        for key in ("announced_on", "started_on", "expected_on", "completed_on"):
            out[key], e = _date(row.get(key, ""), key)
            if e:
                errs.append(e)
        if out.get("status") == "operational" and not out.get("completed_on"):
            warns.append("status is operational but completed_on is empty")
        if out.get("completed_on") and out["completed_on"] > self.today:
            errs.append("completed_on is in the future; use expected_on instead")
        self.point(row, city, out, errs)
        self.provenance(row, out, errs, warns)
        return out, errs, warns


FILE_KINDS = {
    "prices.csv": "prices",
    "rents.csv": "rents",
    "projects.csv": "projects",
    "infra_events.csv": "infra_events",
}


def validate_file(path: Path, checker: RowChecker | None = None) -> Result:
    kind = FILE_KINDS.get(path.name)
    if kind is None:
        raise ValueError(f"don't know how to validate {path.name}; expected one of {list(FILE_KINDS)}")
    checker = checker or RowChecker(load_locality_lookup(), load_cities())
    result = Result()
    seen: set[tuple] = set()
    with open(path, newline="", encoding="utf-8-sig") as f:
        for lineno, row in enumerate(csv.DictReader(f), start=2):
            row = {k.strip(): (v or "") for k, v in row.items() if k}
            if not any(v.strip() for v in row.values()):
                continue
            out, errs, warns = getattr(checker, kind)(row)
            key = tuple(str(out.get(k)) for k in sorted(out) if k not in ("notes", "source_url"))
            if key in seen:
                warns.append("duplicate of an earlier row")
            seen.add(key)
            result.errors.extend(f"{path.name} line {lineno}: {m}" for m in errs)
            result.warnings.extend(f"{path.name} line {lineno}: {m}" for m in warns)
            if not errs:
                out["_line"] = lineno
                result.rows.append(out)
    return result


def main(argv: list[str]) -> int:
    paths = [MANUAL_DIR / a if not Path(a).exists() else Path(a) for a in argv] or \
            [MANUAL_DIR / name for name in FILE_KINDS]
    checker = RowChecker(load_locality_lookup(), load_cities())
    total_errors = 0
    for path in paths:
        if not path.exists():
            continue
        res = validate_file(path, checker)
        print(f"{path.name}: {len(res.rows)} valid rows, "
              f"{len(res.errors)} errors, {len(res.warnings)} warnings")
        for msg in res.errors:
            print(f"  ERROR    {msg}")
        for msg in res.warnings:
            print(f"  warning  {msg}")
        total_errors += len(res.errors)
    return 1 if total_errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
