from leadbot.discovery.query_builder import build_queries

SANTANDER_METRO = {"Bucaramanga", "Floridablanca", "Girón", "Piedecuesta"}


def test_starts_with_santander_and_the_case_study_sectors():
    queries = build_queries(max_priority=1)
    assert {q.city for q in queries} == SANTANDER_METRO
    assert {q.sector for q in queries} == {"repuestos_autopartes", "comercio", "joyeria_relojeria"}


def test_disabled_cities_never_appear():
    cities = {q.city for q in build_queries(max_priority=99)}
    assert "Bogotá" not in cities
    assert "Barrancabermeja" in cities


def test_filters_and_keyword_format():
    queries = build_queries(sectors=["joyeria_relojeria"], cities=["Girón"])
    assert queries
    assert all(q.sector == "joyeria_relojeria" and q.city == "Girón" for q in queries)
    assert queries[0].keyword == "relojerías en Girón"


def test_ordered_by_city_priority():
    queries = build_queries(max_priority=2)
    assert queries[0].city in SANTANDER_METRO
    assert queries[-1].city not in SANTANDER_METRO
