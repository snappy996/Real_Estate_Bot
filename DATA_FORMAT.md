# Manual data format

Four CSV files in `data/manual/` hold everything you collect by hand. Open them in Excel or Google Sheets, add rows, save as CSV (UTF-8), then run:

```
python -m core.ingest.validate      # checks every file, no database needed
python -m core.ingest.load manual   # writes valid rows to Neon
```

Nothing is written if any row has an error, so a typo can't half-load a file. Re-loading the same file is safe: rows are matched on their natural key and updated, never duplicated.

## Rules for every file

| Rule | Example |
|---|---|
| Dates are `YYYY-MM-DD` | `2026-10-30` |
| Periods (the month a price or rent applies to) are `YYYY-MM` | `2026-10` |
| Money is plain rupees; commas and ₹ are fine, lakh/crore words are not | `8500`, `8,500`, `₹8,500` |
| `city` is `Hyderabad` or `Pune` | `Pune` |
| `locality` is the name as you'd say it; spelling variants work | `Kokapeta`, `KPHB`, `Wagoli` |
| `source` is required: the website or document you read | `MagicBricks listing page`, `TS RERA certificate` |
| `collected_on` is required: the day you looked it up | `2026-10-30` |
| Leave a cell empty when you don't know; never write `NA` or `0` | |

Covered localities: **Hyderabad** Gachibowli, Kokapet, Lingampally, Kukatpally, Tellapur · **Pune** Kharadi, Wagholi, Kalyani Nagar, Yerwada, Hadapsar. A new spelling that is rejected goes into `data/seed/locality_aliases.csv`.

## prices.csv — price per sq ft for a locality and month

| Column | Required | Allowed values / format |
|---|---|---|
| city | yes | Hyderabad, Pune |
| locality | yes | name or spelling variant |
| period | yes | `YYYY-MM` |
| property_type | no (default apartment) | apartment, villa, plot, independent_house |
| price_basis | yes | asking (listing price), transaction (registered sale), guideline (Telangana govt value), ready_reckoner (Maharashtra govt rate), index |
| psf_low, psf_median, psf_high | at least one | ₹ per sq ft, between 500 and 100,000 |
| area_basis | no (default super_built_up) | super_built_up, built_up, carpet, plot |
| sample_size | no | how many listings you looked at |
| source, source_url, collected_on, notes | source + collected_on | |

```csv
city,locality,period,property_type,price_basis,psf_low,psf_median,psf_high,area_basis,sample_size,source,source_url,collected_on,notes
Hyderabad,Kokapet,2026-10,apartment,asking,9500,11000,13500,super_built_up,25,Listing portal search,https://example.com/kokapet,2026-10-30,example row: replace
Pune,Kharadi,2026-10,apartment,asking,10500,12000,14500,carpet,18,Listing portal search,,2026-10-30,example row: replace
```

Tip: write down which area the site quotes. Pune listings often quote carpet area, Hyderabad listings usually super built-up, and mixing them makes prices look 25–35% apart when they aren't.

## rents.csv — monthly rent by BHK

| Column | Required | Allowed values / format |
|---|---|---|
| city, locality, period | yes | as above |
| bhk | yes | 1, 2, 2.5, 3, 4 (or `2BHK`) |
| furnishing | no (default unknown) | unfurnished, semi, full, unknown |
| rent_low, rent_median, rent_high | at least one | ₹ per month, between 3,000 and 10,00,000 |
| typical_sqft | no | typical flat size for this BHK |
| sample_size, source, source_url, collected_on, notes | source + collected_on | |

```csv
city,locality,period,bhk,furnishing,rent_low,rent_median,rent_high,typical_sqft,sample_size,source,source_url,collected_on,notes
Hyderabad,Gachibowli,2026-10,2,semi,28000,32000,38000,1200,15,Rental listings,,2026-10-30,example row: replace
Pune,Wagholi,2026-10,2,unfurnished,15000,17000,20000,950,12,Rental listings,,2026-10-30,example row: replace
```

## projects.csv — one row per RERA-registered project

| Column | Required | Allowed values / format |
|---|---|---|
| rera_id | yes | exactly as on the certificate (TS RERA ids start `P02`, MahaRERA Pune ids `P52`) |
| city | yes | Hyderabad, Pune |
| locality | no | name or spelling variant |
| name | yes | project name |
| builder | no | developer name; builders are created automatically |
| lat, lng | no (both or neither) | decimal degrees, latitude first: `17.3960`, `78.3360` (right-click a spot in Google Maps to copy) |
| project_type | no (default apartment) | apartment, villa, plot, mixed, commercial |
| unit_types | no | `2BHK,3BHK,4BHK` |
| total_units | no | whole number |
| psf_min, psf_max | no | ₹ per sq ft |
| launch_date, possession_date | no | `YYYY-MM-DD`; possession as promised on RERA |
| status | no (default unknown) | pre_launch, under_construction, ready, completed, lapsed, unknown |
| source, source_url, collected_on, notes | source + collected_on | |

```csv
rera_id,city,locality,name,builder,lat,lng,project_type,unit_types,total_units,psf_min,psf_max,launch_date,possession_date,status,source,source_url,collected_on,notes
P02400001234,Hyderabad,Kokapet,Example Heights,Example Developers,17.3960,78.3360,apartment,"3BHK,4BHK",600,10500,12500,2025-03-01,2029-06-30,under_construction,TS RERA certificate,,2026-10-30,example row: replace
```

## infra_events.csv — infrastructure that can move prices

One row per project **per status**: when a metro line goes from `announced` to `under_construction`, add a second row rather than editing the first. That history is what the Phase 5 development-impact model learns from.

| Column | Required | Allowed values / format |
|---|---|---|
| city | yes | Hyderabad, Pune |
| locality | no (empty = city-wide) | name or spelling variant |
| event_type | yes | metro, road, flyover, it_park, airport, rail, hospital, school, mall, zoning, other |
| name | yes | specific: `Kharadi metro station`, not `metro` |
| status | yes | announced, approved, under_construction, operational, cancelled |
| announced_on, started_on, expected_on, completed_on | no | `YYYY-MM-DD`; a month is fine as the 1st (`2027-03-01`) |
| lat, lng | no | decimal degrees |
| source, source_url, collected_on, notes | source + collected_on | news article or government notice |

```csv
city,locality,event_type,name,status,announced_on,started_on,expected_on,completed_on,lat,lng,source,source_url,collected_on,notes
Pune,Kharadi,metro,Example metro station,under_construction,2023-01-01,2024-06-01,2027-12-01,,,,News article,https://example.com/news,2026-10-30,example row: replace
```

## What's most valuable to collect first

1. **Asking prices**, current month, all 10 localities (prices.csv). Even one row per locality makes the price tool usable.
2. **Rents** for 2 and 3 BHK in each locality (rents.csv), which unlocks rental yield.
3. **Past prices**: many listing sites and news articles quote "₹X per sq ft in 2021". Each one is a `prices.csv` row with that year's period; this history is what appreciation and the ML models need most.
4. **Metro and road events** with their dates (infra_events.csv).
5. **Stamp duty and registration rates** for Telangana and Maharashtra, from the state registration department sites, into `data/seed/cities.csv`. The cost calculator stays off until these are filled, so it never quotes a guessed rate.

## What not to collect

Bulk-copying listing portals, by script or by hand, breaks their terms. Reading a few pages and recording a price range in your own words, with the source named, is the intended use of these files.
