"""Cliente del servicio gosom/google-maps-scraper (modo web, REST API).

Endpoints usados (spec en http://localhost:8080/api/docs):
    POST /api/v1/jobs                 {"name", "keywords": [...], "lang", "zoom", "depth", "email", "max_time"}
    GET  /api/v1/jobs/{id}            estado del trabajo
    GET  /api/v1/jobs/{id}/download   resultados en CSV
"""

from dataclasses import dataclass

from leadbot.discovery.query_builder import SearchQuery


@dataclass
class GmapsJob:
    id: str
    status: str


class GmapsClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def create_job(self, query: SearchQuery, *, depth: int = 1, extract_email: bool = True) -> GmapsJob:
        raise NotImplementedError("F1")

    def get_job(self, job_id: str) -> GmapsJob:
        raise NotImplementedError("F1")

    def download_results(self, job_id: str) -> list[dict]:
        """Descarga el CSV y lo devuelve como filas (title, phone, website, emails, place_id...)."""
        raise NotImplementedError("F1")
