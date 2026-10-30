"""Arma las búsquedas de Google Maps cruzando sectores × ciudades de config/."""

from dataclasses import dataclass
from pathlib import Path

from leadbot.core.config import CONFIG_DIR, load_yaml


@dataclass(frozen=True)
class SearchQuery:
    sector: str
    city: str
    term: str

    @property
    def keyword(self) -> str:
        return f"{self.term} en {self.city}"


def build_queries(
    *,
    sectors: list[str] | None = None,
    cities: list[str] | None = None,
    max_priority: int = 3,
    config_dir: Path = CONFIG_DIR,
) -> list[SearchQuery]:
    """Búsquedas ordenadas por prioridad de ciudad. Filtra por sector o ciudad si se indican."""
    sector_cfg = load_yaml("sectors", config_dir)["sectors"]
    city_cfg = load_yaml("cities", config_dir)["cities"]

    active_cities = sorted(
        (
            c
            for c in city_cfg
            if c["enabled"]
            and c["priority"] <= max_priority
            and (cities is None or c["name"] in cities)
        ),
        key=lambda c: c["priority"],
    )
    active_sectors = {
        key: s
        for key, s in sector_cfg.items()
        if s["enabled"] and (sectors is None or key in sectors)
    }

    return [
        SearchQuery(sector=key, city=city["name"], term=term)
        for city in active_cities
        for key, sector in active_sectors.items()
        for term in sector["terms"]
    ]
