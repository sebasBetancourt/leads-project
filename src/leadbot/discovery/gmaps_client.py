"""Cliente del servicio gosom/google-maps-scraper (modo web, REST API).

Endpoints usados (spec en http://localhost:8080/api/docs):
    POST /api/v1/jobs                 {"name", "keywords": [...], "lang", "zoom", "depth", "email", "max_time"}
    GET  /api/v1/jobs/{id}            estado del trabajo: pending | working | ok | failed
    GET  /api/v1/jobs/{id}/download   resultados en CSV

Ojo: la respuesta real usa claves con mayúscula ("ID", "Status") aunque la spec diga "id", "status".
Un trabajo por búsqueda: el CSV no dice qué keyword produjo cada fila.
"""

import csv
import io
import time
from dataclasses import dataclass, field

import httpx

from leadbot.discovery.query_builder import SearchQuery

# Columnas pesadas del CSV que no guardamos en companies.raw.
HEAVY_COLUMNS = {"user_reviews", "user_reviews_extended", "images", "popular_times", "reviews_per_rating"}


@dataclass
class GmapsJob:
    id: str
    status: str

    @property
    def finished(self) -> bool:
        return self.status in ("ok", "failed")


@dataclass
class GmapsPlace:
    place_id: str | None
    name: str
    category: str | None
    address: str | None
    phone: str | None
    website: str | None
    rating: float | None
    reviews_count: int | None
    emails: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


def _get(data: dict, key: str):
    return data.get(key, data.get(key.capitalize(), data.get(key.upper())))


def _to_float(value: str) -> float | None:
    try:
        return float(value) if value else None
    except ValueError:
        return None


def _to_int(value: str) -> int | None:
    try:
        return int(float(value)) if value else None
    except ValueError:
        return None


def parse_row(row: dict[str, str]) -> GmapsPlace:
    return GmapsPlace(
        place_id=row.get("place_id") or None,
        name=row.get("title", "").strip(),
        category=row.get("category") or None,
        address=row.get("address") or None,
        phone=row.get("phone") or None,
        website=row.get("website") or None,
        rating=_to_float(row.get("review_rating", "")),
        reviews_count=_to_int(row.get("review_count", "")),
        emails=[e.strip().lower() for e in row.get("emails", "").split(",") if e.strip()],
        raw={k: v for k, v in row.items() if k not in HEAVY_COLUMNS and v},
    )


class GmapsClient:
    def __init__(self, base_url: str, *, timeout: float = 30.0):
        self.http = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)

    def create_job(
        self, query: SearchQuery, *, depth: int = 1, extract_email: bool = True, max_time: int = 600
    ) -> GmapsJob:
        response = self.http.post(
            "/api/v1/jobs",
            json={
                "name": query.keyword,
                "keywords": [query.keyword],
                "lang": "es",
                "zoom": 15,
                "depth": depth,
                "email": extract_email,
                "max_time": max_time,  # segundos
            },
        )
        response.raise_for_status()
        return GmapsJob(id=_get(response.json(), "id"), status="pending")

    def get_job(self, job_id: str) -> GmapsJob:
        response = self.http.get(f"/api/v1/jobs/{job_id}")
        response.raise_for_status()
        data = response.json()
        return GmapsJob(id=job_id, status=_get(data, "status"))

    def wait(self, job_id: str, *, poll_seconds: float = 10, timeout: float = 900) -> GmapsJob:
        deadline = time.monotonic() + timeout
        while True:
            job = self.get_job(job_id)
            if job.finished or time.monotonic() > deadline:
                return job
            time.sleep(poll_seconds)

    def download_results(self, job_id: str) -> list[GmapsPlace]:
        response = self.http.get(f"/api/v1/jobs/{job_id}/download")
        response.raise_for_status()
        rows = csv.DictReader(io.StringIO(response.content.decode("utf-8-sig")))
        return [place for place in map(parse_row, rows) if place.name]
