-- 001_init.sql — core schema for the real estate agent
-- Target: Neon Postgres (AWS Singapore), Postgres 16+
-- Every fact table carries city_id, source and collected_on so the agent can
-- always say where a number came from and how old it is.

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ---------------------------------------------------------------------------
-- Reference data
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS cities (
    city_id              TEXT PRIMARY KEY,            -- 'hyd', 'pune'
    name                 TEXT NOT NULL,
    state                TEXT NOT NULL,
    rera_portal_url      TEXT,
    -- Purchase-cost rates in percent. Left NULL until verified from the
    -- state registration department; the cost calculator refuses to run
    -- while they are NULL rather than guessing.
    stamp_duty_pct       NUMERIC(5,2),
    registration_pct     NUMERIC(5,2),
    registration_cap_inr NUMERIC(12,0),               -- e.g. a flat cap, if any
    other_duty_pct       NUMERIC(5,2),                -- transfer duty, metro cess
    rates_source         TEXT,
    rates_verified_on    DATE
);

CREATE TABLE IF NOT EXISTS localities (
    locality_id   TEXT PRIMARY KEY,                   -- 'hyd-gachibowli'
    city_id       TEXT NOT NULL REFERENCES cities(city_id),
    name          TEXT NOT NULL,
    zone          TEXT,
    centroid      GEOGRAPHY(POINT, 4326),
    boundary      GEOGRAPHY(MULTIPOLYGON, 4326),      -- filled later from OSM
    is_covered    BOOLEAN NOT NULL DEFAULT TRUE,      -- covered by the chat MVP
    centroid_source TEXT,
    UNIQUE (city_id, name)
);
CREATE INDEX IF NOT EXISTS localities_centroid_gix ON localities USING GIST (centroid);
CREATE INDEX IF NOT EXISTS localities_name_trgm ON localities USING GIN (name gin_trgm_ops);

CREATE TABLE IF NOT EXISTS locality_aliases (
    alias        TEXT NOT NULL,
    locality_id  TEXT NOT NULL REFERENCES localities(locality_id) ON DELETE CASCADE,
    PRIMARY KEY (alias, locality_id)
);
CREATE INDEX IF NOT EXISTS locality_aliases_trgm ON locality_aliases USING GIN (alias gin_trgm_ops);

-- ---------------------------------------------------------------------------
-- Market data (quantitative engine)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS price_history (
    id              BIGSERIAL PRIMARY KEY,
    locality_id     TEXT NOT NULL REFERENCES localities(locality_id),
    period          DATE NOT NULL,                    -- first day of the month observed
    property_type   TEXT NOT NULL CHECK (property_type IN
                        ('apartment', 'villa', 'plot', 'independent_house')),
    price_basis     TEXT NOT NULL CHECK (price_basis IN
                        ('asking', 'transaction', 'guideline', 'ready_reckoner', 'index')),
    psf_low         NUMERIC(10,0),                    -- INR per sq ft (super built-up unless noted)
    psf_median      NUMERIC(10,0),
    psf_high        NUMERIC(10,0),
    area_basis      TEXT NOT NULL DEFAULT 'super_built_up'
                        CHECK (area_basis IN ('super_built_up', 'built_up', 'carpet', 'plot')),
    sample_size     INTEGER,
    source          TEXT NOT NULL,
    source_url      TEXT,
    collected_on    DATE NOT NULL,
    collected_by    TEXT NOT NULL DEFAULT 'manual',  -- 'manual' or a scraper name
    notes           TEXT,
    CHECK (psf_low IS NULL OR psf_high IS NULL OR psf_low <= psf_high),
    UNIQUE (locality_id, period, property_type, price_basis, area_basis, source)
);

CREATE TABLE IF NOT EXISTS rent_history (
    id              BIGSERIAL PRIMARY KEY,
    locality_id     TEXT NOT NULL REFERENCES localities(locality_id),
    period          DATE NOT NULL,
    bhk             NUMERIC(3,1) NOT NULL,            -- 1, 2, 2.5, 3 ...
    furnishing      TEXT NOT NULL DEFAULT 'unknown'
                        CHECK (furnishing IN ('unfurnished', 'semi', 'full', 'unknown')),
    rent_low        NUMERIC(10,0),                    -- INR per month
    rent_median     NUMERIC(10,0),
    rent_high       NUMERIC(10,0),
    typical_sqft    NUMERIC(7,0),
    sample_size     INTEGER,
    source          TEXT NOT NULL,
    source_url      TEXT,
    collected_on    DATE NOT NULL,
    collected_by    TEXT NOT NULL DEFAULT 'manual',
    notes           TEXT,
    CHECK (rent_low IS NULL OR rent_high IS NULL OR rent_low <= rent_high),
    UNIQUE (locality_id, period, bhk, furnishing, source)
);

-- ---------------------------------------------------------------------------
-- Projects and builders
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS builders (
    builder_id      TEXT PRIMARY KEY,                 -- slug, 'my-home-constructions'
    name            TEXT NOT NULL,
    website         TEXT,
    rera_complaints INTEGER,
    notes           TEXT,
    source          TEXT NOT NULL,
    collected_on    DATE NOT NULL
);
CREATE INDEX IF NOT EXISTS builders_name_trgm ON builders USING GIN (name gin_trgm_ops);

CREATE TABLE IF NOT EXISTS projects (
    rera_id          TEXT PRIMARY KEY,                -- as printed on the RERA certificate
    city_id          TEXT NOT NULL REFERENCES cities(city_id),
    locality_id      TEXT REFERENCES localities(locality_id),
    name             TEXT NOT NULL,
    builder_id       TEXT REFERENCES builders(builder_id),
    location         GEOGRAPHY(POINT, 4326),
    project_type     TEXT CHECK (project_type IN
                         ('apartment', 'villa', 'plot', 'mixed', 'commercial')),
    unit_types       TEXT,                            -- '2BHK,3BHK,4BHK'
    total_units      INTEGER,
    psf_min          NUMERIC(10,0),
    psf_max          NUMERIC(10,0),
    launch_date      DATE,
    possession_date  DATE,                            -- as promised on RERA
    status           TEXT CHECK (status IN
                         ('pre_launch', 'under_construction', 'ready', 'completed', 'lapsed', 'unknown')),
    source           TEXT NOT NULL,
    source_url       TEXT,
    collected_on     DATE NOT NULL,
    collected_by     TEXT NOT NULL DEFAULT 'manual',
    notes            TEXT,
    search_tsv       TSVECTOR GENERATED ALWAYS AS (
                         to_tsvector('simple', coalesce(name, '') || ' ' || coalesce(rera_id, ''))
                     ) STORED
);
CREATE INDEX IF NOT EXISTS projects_location_gix ON projects USING GIST (location);
CREATE INDEX IF NOT EXISTS projects_name_trgm ON projects USING GIN (name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS projects_tsv ON projects USING GIN (search_tsv);
CREATE INDEX IF NOT EXISTS projects_locality ON projects (locality_id);

-- ---------------------------------------------------------------------------
-- Infrastructure events (bridge from news to the quant engine)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS infra_events (
    id              BIGSERIAL PRIMARY KEY,
    city_id         TEXT NOT NULL REFERENCES cities(city_id),
    locality_id     TEXT REFERENCES localities(locality_id),   -- NULL if city-wide
    event_type      TEXT NOT NULL CHECK (event_type IN
                        ('metro', 'road', 'flyover', 'it_park', 'airport', 'rail',
                         'hospital', 'school', 'mall', 'zoning', 'other')),
    name            TEXT NOT NULL,                    -- 'Kokapet metro station'
    status          TEXT NOT NULL CHECK (status IN
                        ('announced', 'approved', 'under_construction', 'operational', 'cancelled')),
    announced_on    DATE,
    started_on      DATE,
    expected_on     DATE,
    completed_on    DATE,
    location        GEOGRAPHY(POINT, 4326),
    source          TEXT NOT NULL,
    source_url      TEXT,
    collected_on    DATE NOT NULL,
    collected_by    TEXT NOT NULL DEFAULT 'manual',
    notes           TEXT,
    UNIQUE (city_id, name, status)
);
CREATE INDEX IF NOT EXISTS infra_events_location_gix ON infra_events USING GIST (location);

-- ---------------------------------------------------------------------------
-- Amenities from OpenStreetMap (used for comparisons and commute)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS amenities (
    osm_id        TEXT PRIMARY KEY,                   -- 'node/123', 'way/456'
    city_id       TEXT NOT NULL REFERENCES cities(city_id),
    category      TEXT NOT NULL,                      -- school, hospital, metro_station ...
    name          TEXT,
    location      GEOGRAPHY(POINT, 4326) NOT NULL,
    tags          JSONB,
    collected_on  DATE NOT NULL
);
CREATE INDEX IF NOT EXISTS amenities_location_gix ON amenities USING GIST (location);
CREATE INDEX IF NOT EXISTS amenities_category ON amenities (city_id, category);

-- ---------------------------------------------------------------------------
-- Hybrid RAG
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS doc_chunks (
    id               BIGSERIAL PRIMARY KEY,
    city_id          TEXT REFERENCES cities(city_id),
    locality_id      TEXT REFERENCES localities(locality_id),
    rera_id          TEXT REFERENCES projects(rera_id),
    doc_type         TEXT NOT NULL,                   -- news, brochure, rera_doc, master_plan
    title            TEXT,
    content          TEXT NOT NULL,
    source_url       TEXT,
    published_on     DATE,
    embedding        VECTOR(768),
    embedding_model  TEXT,                            -- 'gemini-embedding-2'
    embedding_dim    INTEGER,
    content_tsv      TSVECTOR GENERATED ALWAYS AS (
                         to_tsvector('simple', coalesce(title, '') || ' ' || content)
                     ) STORED,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS doc_chunks_embedding_hnsw
    ON doc_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS doc_chunks_tsv ON doc_chunks USING GIN (content_tsv);

-- ---------------------------------------------------------------------------
-- Scraper bookkeeping: fetch once, parse many times
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS raw_pages (
    id             BIGSERIAL PRIMARY KEY,
    source         TEXT NOT NULL,                     -- scraper name, 'osm', 'ts_rera'
    url            TEXT NOT NULL,
    request_key    TEXT NOT NULL,                     -- url + body for POSTs, hashed
    fetched_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    http_status    INTEGER,
    content_type   TEXT,
    content_hash   TEXT NOT NULL,                     -- sha256 of the body
    body_gz        BYTEA,                             -- gzip-compressed body
    parsed_at      TIMESTAMPTZ,
    parse_error    TEXT,
    UNIQUE (source, request_key, content_hash)
);
CREATE INDEX IF NOT EXISTS raw_pages_latest ON raw_pages (source, request_key, fetched_at DESC);

CREATE TABLE IF NOT EXISTS scrape_runs (
    id           BIGSERIAL PRIMARY KEY,
    source       TEXT NOT NULL,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ,
    fetched      INTEGER NOT NULL DEFAULT 0,
    unchanged    INTEGER NOT NULL DEFAULT 0,
    failed       INTEGER NOT NULL DEFAULT 0,
    notes        TEXT
);

-- ---------------------------------------------------------------------------
-- Name resolution helper: 'Kokapeta' -> hyd-kokapet
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION resolve_locality(q TEXT, in_city TEXT DEFAULT NULL)
RETURNS TABLE (locality_id TEXT, name TEXT, score REAL)
LANGUAGE sql STABLE AS $$
    WITH candidates AS (
        SELECT l.locality_id, l.name, similarity(l.name, q) AS score
        FROM localities l
        WHERE in_city IS NULL OR l.city_id = in_city
        UNION ALL
        SELECT l.locality_id, l.name, similarity(a.alias, q) AS score
        FROM locality_aliases a JOIN localities l USING (locality_id)
        WHERE in_city IS NULL OR l.city_id = in_city
    )
    SELECT locality_id, name, max(score) AS score
    FROM candidates
    WHERE score > 0.3
    GROUP BY locality_id, name
    ORDER BY score DESC
    LIMIT 5;
$$;
