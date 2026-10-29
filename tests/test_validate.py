"""Tests for the manual CSV validator. Run: python -m unittest discover tests"""

import re
import tempfile
import unittest
from datetime import date
from pathlib import Path

from core.ingest.validate import (ROOT, RowChecker, load_cities, load_locality_lookup,
                                  validate_file)

TODAY = date(2026, 10, 30)


def checker():
    return RowChecker(load_locality_lookup(), load_cities(), today=TODAY)


def write(name: str, text: str) -> Path:
    d = Path(tempfile.mkdtemp())
    path = d / name
    path.write_text(text.strip() + "\n", encoding="utf-8")
    return path


class LocalityResolution(unittest.TestCase):
    def test_names_aliases_and_ids_resolve(self):
        lookup = load_locality_lookup()
        cases = {
            ("hyd", "kokapeta"): "hyd-kokapet",
            ("hyd", "kphb"): "hyd-kukatpally",
            ("hyd", "gachibowli"): "hyd-gachibowli",
            ("pune", "wagoli"): "pune-wagholi",
            ("pune", "kalyaninagar"): "pune-kalyani-nagar",
            ("pune", "yerawada"): "pune-yerwada",
        }
        for key, expected in cases.items():
            self.assertEqual(lookup.get(key), expected, key)

    def test_all_ten_localities_seeded(self):
        lookup = load_locality_lookup()
        self.assertEqual(len(set(lookup.values())), 10)

    def test_locality_in_wrong_city_rejected(self):
        res = validate_file(write("prices.csv", """
city,locality,period,price_basis,psf_median,source,collected_on
Pune,Kokapet,2026-10,asking,9000,test,2026-10-30
"""), checker())
        self.assertEqual(len(res.rows), 0)
        self.assertIn("unknown locality", res.errors[0])


class Prices(unittest.TestCase):
    def test_valid_row_with_rupee_and_commas(self):
        res = validate_file(write("prices.csv", """
city,locality,period,property_type,price_basis,psf_low,psf_median,psf_high,area_basis,sample_size,source,source_url,collected_on,notes
Hyderabad,Kokapeta,2026-10,,asking,"₹9,500","11,000",13500,,25,portal,https://x.test,2026-10-30,
"""), checker())
        self.assertEqual(res.errors, [])
        row = res.rows[0]
        self.assertEqual(row["locality_id"], "hyd-kokapet")
        self.assertEqual(row["period"], date(2026, 10, 1))
        self.assertEqual(row["psf_low"], 9500)
        self.assertEqual(row["property_type"], "apartment")
        self.assertEqual(row["area_basis"], "super_built_up")

    def test_typo_extra_zero_and_inverted_range(self):
        res = validate_file(write("prices.csv", """
city,locality,period,price_basis,psf_low,psf_high,source,collected_on
Pune,Kharadi,2026-10,asking,120000,9000,portal,2026-10-30
"""), checker())
        self.assertEqual(len(res.rows), 0)
        joined = " ".join(res.errors)
        self.assertIn("outside the expected", joined)
        self.assertIn("greater than", joined)

    def test_missing_source_and_bad_period(self):
        res = validate_file(write("prices.csv", """
city,locality,period,price_basis,psf_median,source,collected_on
Pune,Kharadi,10/2026,asking,12000,,2026-10-30
"""), checker())
        joined = " ".join(res.errors)
        self.assertIn("YYYY-MM", joined)
        self.assertIn("source is required", joined)

    def test_future_collected_on(self):
        res = validate_file(write("prices.csv", """
city,locality,period,price_basis,psf_median,source,collected_on
Pune,Kharadi,2026-10,asking,12000,portal,2027-01-01
"""), checker())
        self.assertIn("future", res.errors[0])


class Rents(unittest.TestCase):
    def test_bhk_text_and_default_furnishing(self):
        res = validate_file(write("rents.csv", """
city,locality,period,bhk,rent_median,source,collected_on
Hyderabad,Gachibowli,2026-10,2BHK,32000,listings,2026-10-30
"""), checker())
        self.assertEqual(res.errors, [])
        self.assertEqual(res.rows[0]["bhk"], 2)
        self.assertEqual(res.rows[0]["furnishing"], "unknown")

    def test_yearly_rent_typed_by_mistake(self):
        res = validate_file(write("rents.csv", """
city,locality,period,bhk,rent_median,source,collected_on
Pune,Wagholi,2026-10,2,2040000,listings,2026-10-30
"""), checker())
        self.assertIn("monthly rent", res.errors[0])


class Projects(unittest.TestCase):
    def test_swapped_lat_lng_caught(self):
        res = validate_file(write("projects.csv", """
rera_id,city,locality,name,lat,lng,source,collected_on
P02400001234,Hyderabad,Kokapet,Test,78.3360,17.3960,RERA,2026-10-30
"""), checker())
        self.assertIn("outside hyd", res.errors[0])

    def test_launch_after_possession(self):
        res = validate_file(write("projects.csv", """
rera_id,city,name,launch_date,possession_date,source,collected_on
P52100001234,Pune,Test,2029-01-01,2027-01-01,RERA,2026-10-30
"""), checker())
        self.assertIn("launch_date is after", res.errors[0])

    def test_prefix_warning_not_error(self):
        res = validate_file(write("projects.csv", """
rera_id,city,name,source,collected_on
P99000001234,Pune,Test,RERA,2026-10-30
"""), checker())
        self.assertEqual(res.errors, [])
        self.assertTrue(res.warnings)


class InfraEvents(unittest.TestCase):
    def test_completed_in_future_rejected(self):
        res = validate_file(write("infra_events.csv", """
city,locality,event_type,name,status,completed_on,source,collected_on
Pune,Kharadi,metro,Kharadi station,operational,2027-05-01,news,2026-10-30
"""), checker())
        self.assertIn("in the future", res.errors[0])

    def test_city_wide_event_ok(self):
        res = validate_file(write("infra_events.csv", """
city,locality,event_type,name,status,expected_on,source,collected_on
Hyderabad,,road,Example ring road phase 2,announced,2028-01-01,news,2026-10-30
"""), checker())
        self.assertEqual(res.errors, [])
        self.assertIsNone(res.rows[0]["locality_id"])


class FormatGuideExamples(unittest.TestCase):
    """Every example CSV block in DATA_FORMAT.md must pass validation."""

    def test_examples_validate(self):
        text = (ROOT / "DATA_FORMAT.md").read_text(encoding="utf-8")
        blocks = re.findall(r"```csv\n(.*?)```", text, re.S)
        self.assertEqual(len(blocks), 4)
        names = ["prices.csv", "rents.csv", "projects.csv", "infra_events.csv"]
        for name, block in zip(names, blocks):
            res = validate_file(write(name, block), checker())
            self.assertEqual(res.errors, [], name)
            self.assertTrue(res.rows, name)


class TemplatesHaveMatchingHeaders(unittest.TestCase):
    def test_template_headers_match_guide(self):
        text = (ROOT / "DATA_FORMAT.md").read_text(encoding="utf-8")
        blocks = re.findall(r"```csv\n(.*?)```", text, re.S)
        for name, block in zip(["prices.csv", "rents.csv", "projects.csv", "infra_events.csv"], blocks):
            template = (ROOT / "data" / "manual" / name).read_text(encoding="utf-8").strip()
            self.assertEqual(template, block.splitlines()[0], name)


if __name__ == "__main__":
    unittest.main()
