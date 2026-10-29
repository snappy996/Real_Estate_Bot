"""Scraper framework: fetch once, store the raw copy, parse many times.

Every scraper subclasses `Scraper` and implements `requests()` (what to fetch)
and `parse()` (raw body -> rows written to the database).

    fetch   -> raw_pages (gzip body + sha256 hash)
    parse   -> reads raw_pages, writes the real tables with upserts

Re-running a scraper re-fetches a page only when its cached copy is older
than `max_age`, and re-parses only when the content hash changed. Fixing a
parser is `python -m core.ingest.scrape <name> --reparse`, with no network.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import time
import urllib.robotparser
from dataclasses import dataclass, field
from datetime import timedelta
from urllib.parse import urlsplit

from core.config import settings


@dataclass
class Request:
    url: str
    method: str = "GET"
    data: dict | str | None = None          # form fields or a raw body for POST
    headers: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)  # passed through to parse()

    @property
    def key(self) -> str:
        body = self.data if isinstance(self.data, str) else json.dumps(self.data, sort_keys=True)
        return hashlib.sha256(f"{self.method} {self.url} {body}".encode()).hexdigest()


@dataclass
class RawPage:
    id: int
    request: Request
    body: bytes
    content_type: str | None
    changed: bool

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.body)


class Scraper:
    name: str = "base"
    max_age: timedelta = timedelta(days=30)
    delay_seconds: float = settings.scraper_delay_seconds
    respect_robots: bool = True

    def __init__(self, conn, force: bool = False):
        self.conn = conn
        self.force = force
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}
        self._last_request_at = 0.0
        self._client = None
        self.stats = {"fetched": 0, "unchanged": 0, "cached": 0, "failed": 0, "rows": 0}

    # -- subclasses implement these -----------------------------------------

    def requests(self):
        """Yield Request objects to fetch."""
        raise NotImplementedError

    def parse(self, page: RawPage) -> int:
        """Write rows for one raw page; return how many rows were written."""
        raise NotImplementedError

    # -- network -------------------------------------------------------------

    @property
    def client(self):
        if self._client is None:
            import httpx

            self._client = httpx.Client(
                headers={"User-Agent": settings.scraper_user_agent},
                timeout=60, follow_redirects=True,
            )
        return self._client

    def allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        parts = urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}"
        if base not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                resp = self.client.get(f"{base}/robots.txt")
                rp.parse(resp.text.splitlines() if resp.status_code == 200 else [])
            except Exception:
                rp.parse([])
            self._robots[base] = rp
        return self._robots[base].can_fetch(settings.scraper_user_agent, url)

    def _polite_wait(self):
        wait = self.delay_seconds - (time.monotonic() - self._last_request_at)
        if wait > 0:
            time.sleep(wait)
        self._last_request_at = time.monotonic()

    def _download(self, req: Request, attempts: int = 4):
        delay = 5.0
        for attempt in range(1, attempts + 1):
            self._polite_wait()
            try:
                if req.method == "POST":
                    kwargs = {"content": req.data} if isinstance(req.data, str) else {"data": req.data}
                    resp = self.client.post(req.url, headers=req.headers, **kwargs)
                else:
                    resp = self.client.get(req.url, headers=req.headers, params=req.data)
                if resp.status_code in (429, 502, 503, 504) and attempt < attempts:
                    retry_after = resp.headers.get("retry-after", "")
                    time.sleep(float(retry_after) if retry_after.isdigit() else delay)
                    delay *= 2
                    continue
                return resp
            except Exception:
                if attempt == attempts:
                    raise
                time.sleep(delay)
                delay *= 2

    # -- raw store -----------------------------------------------------------

    def _latest(self, req: Request):
        return self.conn.execute(
            """
            SELECT id, content_hash, fetched_at, body_gz, content_type
            FROM raw_pages WHERE source = %s AND request_key = %s
            ORDER BY fetched_at DESC LIMIT 1
            """,
            [self.name, req.key],
        ).fetchone()

    def fetch(self, req: Request) -> RawPage | None:
        latest = self._latest(req)
        if latest and not self.force:
            (rid, _hash, fetched_at, body_gz, ctype) = latest
            age = self.conn.execute("SELECT now() - %s", [fetched_at]).fetchone()[0]
            if age < self.max_age:
                self.stats["cached"] += 1
                return RawPage(rid, req, gzip.decompress(body_gz), ctype, changed=False)

        if not self.allowed(req.url):
            print(f"  skip (robots.txt disallows): {req.url}")
            return None
        try:
            resp = self._download(req)
        except Exception as exc:
            self.stats["failed"] += 1
            print(f"  failed: {req.url}: {exc}")
            return None
        if resp.status_code != 200:
            self.stats["failed"] += 1
            print(f"  HTTP {resp.status_code}: {req.url}")
            return None

        body = resp.content
        digest = hashlib.sha256(body).hexdigest()
        if latest and latest[1] == digest:
            self.conn.execute("UPDATE raw_pages SET fetched_at = now() WHERE id = %s", [latest[0]])
            self.stats["unchanged"] += 1
            return RawPage(latest[0], req, body, latest[4], changed=False)

        rid = self.conn.execute(
            """
            INSERT INTO raw_pages (source, url, request_key, http_status, content_type,
                                   content_hash, body_gz)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (source, request_key, content_hash)
                DO UPDATE SET fetched_at = now()
            RETURNING id
            """,
            [self.name, req.url, req.key, resp.status_code,
             resp.headers.get("content-type"), digest, gzip.compress(body)],
        ).fetchone()[0]
        self.stats["fetched"] += 1
        return RawPage(rid, req, body, resp.headers.get("content-type"), changed=True)

    def _mark_parsed(self, page: RawPage, error: str | None = None):
        self.conn.execute(
            "UPDATE raw_pages SET parsed_at = now(), parse_error = %s WHERE id = %s",
            [error, page.id],
        )

    # -- runs ----------------------------------------------------------------

    def run(self) -> dict:
        run_id = self.conn.execute(
            "INSERT INTO scrape_runs (source) VALUES (%s) RETURNING id", [self.name]
        ).fetchone()[0]
        for req in self.requests():
            page = self.fetch(req)
            if page is None:
                continue
            already = self.conn.execute(
                "SELECT parsed_at IS NOT NULL AND parse_error IS NULL FROM raw_pages WHERE id = %s",
                [page.id],
            ).fetchone()[0]
            if page.changed or not already:
                self._parse_safely(page)
            self.conn.commit()  # keep progress if a later page fails
        self.conn.execute(
            """UPDATE scrape_runs SET finished_at = now(), fetched = %s, unchanged = %s,
               failed = %s, notes = %s WHERE id = %s""",
            [self.stats["fetched"], self.stats["unchanged"] + self.stats["cached"],
             self.stats["failed"], json.dumps(self.stats), run_id],
        )
        self.conn.commit()
        return self.stats

    def reparse(self) -> dict:
        """Re-run parse() over the latest stored copy of every request. No network."""
        rows = self.conn.execute(
            """
            SELECT DISTINCT ON (request_key) id, request_key, url, body_gz, content_type
            FROM raw_pages WHERE source = %s
            ORDER BY request_key, fetched_at DESC
            """,
            [self.name],
        ).fetchall()
        # Rebuild the Request objects (offline) so parse() gets the same meta it
        # had at fetch time; pages no longer requested are parsed without meta.
        known = {r.key: r for r in self.requests()}
        for rid, key, url, body_gz, ctype in rows:
            req = known.get(key, Request(url))
            self._parse_safely(RawPage(rid, req, gzip.decompress(body_gz), ctype, changed=True))
        self.conn.commit()
        return self.stats

    def _parse_safely(self, page: RawPage):
        try:
            with self.conn.transaction():
                self.stats["rows"] += self.parse(page)
            self._mark_parsed(page)
        except Exception as exc:
            self._mark_parsed(page, f"{type(exc).__name__}: {exc}")
            print(f"  parse error on {page.request.url}: {exc}")
