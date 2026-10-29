# Real Estate AI Agent

AI assistant for homebuyers in Hyderabad and Pune: prices, projects, true cost, appreciation scenarios and rental yields, with every number coming from the database, not the model.

This is the Phase 1 starter: database schema, seed data, manual-data templates with a validator, and the scraper framework.

## Setup (Python 3.12)

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # then paste your Neon connection string
```

In Neon, create a project in **AWS Asia Pacific (Singapore)**, then copy the *pooled* connection string into `DATABASE_URL`. API keys can wait until the LLM step.

```bash
python -m core.ingest.setup        # creates tables, loads cities + 10 localities
python -m unittest discover tests  # 16 validator tests
```

If `setup` finishes, PostGIS, pgvector and pg_trgm are all enabled (the schema needs all three). Its last line checks that the misspelling "Kokapeta" resolves to Kokapet.

## Collecting data

| What | How |
|---|---|
| Amenities (schools, hospitals, metro, malls, IT parks) around all 10 localities | `python -m core.ingest.scrape osm` |
| Prices, rents, projects, infrastructure events | Fill `data/manual/*.csv` by hand, following [DATA_FORMAT.md](DATA_FORMAT.md) |
| Check your CSVs (no database needed) | `python -m core.ingest.validate` |
| Load them into Neon | `python -m core.ingest.load manual` |
| RERA portals, guideline values, ready reckoner | Need a saved sample page first; see `core/ingest/scrapers/manual_sources.py` |

The OSM scraper makes about 110 requests (10 localities × 11 categories) five seconds apart, so the first run takes roughly 10 minutes. Re-runs within 90 days use the stored copies and finish in seconds.

### How the scraper avoids re-scraping

Every response is stored compressed in `raw_pages` with a content hash. A re-run fetches a page again only when its stored copy is older than the source's refresh window (90 days for OSM), and re-parses only when the content changed. After fixing a parser:

```bash
python -m core.ingest.scrape osm --reparse    # rebuilds tables from stored pages, no network
```

## Layout

```
core/
  config.py              settings and model names (from .env)
  db.py                  Neon connection, migrations
  ingest/
    validate.py          manual CSV checks (stdlib only)
    load.py              seeds + manual CSVs -> Neon (upserts)
    setup.py             one-time database setup
    scrape.py            scraper CLI
    scrapers/
      base.py            fetch once, store raw, parse many times
      osm.py             OpenStreetMap amenities (ready)
      manual_sources.py  RERA and government-rate sources (need sample pages)
db/migrations/001_init.sql
data/seed/               cities, localities, aliases
data/manual/             your hand-collected CSVs
DATA_FORMAT.md           column-by-column format guide
```

## Before the cost calculator can run

Fill the stamp duty and registration columns in `data/seed/cities.csv` from the Telangana and Maharashtra registration department sites, then re-run `python -m core.ingest.load seeds`. They are left empty on purpose so the app never quotes a guessed rate.

Locality centroids are approximate (`centroid_source = approximate`) and will be refined from OpenStreetMap boundaries.
