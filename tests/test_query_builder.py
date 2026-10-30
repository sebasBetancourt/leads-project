from leadbot.discovery.query_builder import build_queries


def test_only_enabled_sectors_and_priority_cities():
    queries = build_queries(max_priority=1)
    sectors = {q.sector for q in queries}
    cities = {q.city for q in queries}

    assert "repuestos_autopartes" not in sectors  # desactivado en sectors.yaml
    assert cities == {"Bogotá", "Medellín", "Cali", "Barranquilla"}


def test_filters_and_keyword_format():
    queries = build_queries(sectors=["educacion"], cities=["Pereira"])
    assert queries
    assert all(q.sector == "educacion" and q.city == "Pereira" for q in queries)
    assert queries[0].keyword == "academias en Pereira"


def test_ordered_by_city_priority():
    queries = build_queries(max_priority=3)
    assert queries[0].city in {"Bogotá", "Medellín", "Cali", "Barranquilla"}
    assert queries[-1].city not in {"Bogotá", "Medellín", "Cali", "Barranquilla"}
