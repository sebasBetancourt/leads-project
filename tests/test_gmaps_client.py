from leadbot.discovery.gmaps_client import parse_row

ROW = {
    "title": "ELECTROMOTO",
    "category": "Tienda de repuestos para motos",
    "address": "Cra 21 #21-58, Bucaramanga, Santander",
    "phone": "(607) 6340005",
    "website": "https://linktr.ee/Electromoto",
    "review_count": "19",
    "review_rating": "3.500000",
    "place_id": "ChIJC-p9Q3sVaI4RQbH9QzPft9Y",
    "emails": "Electromotola21@gmail.com, otro@motos.co",
    "user_reviews": "[... muy largo ...]",
    "images": "",
}


def test_parse_row_maps_gosom_columns():
    place = parse_row(ROW)
    assert place.name == "ELECTROMOTO"
    assert place.place_id == "ChIJC-p9Q3sVaI4RQbH9QzPft9Y"
    assert place.rating == 3.5
    assert place.reviews_count == 19
    assert place.emails == ["electromotola21@gmail.com", "otro@motos.co"]


def test_parse_row_drops_heavy_and_empty_columns():
    raw = parse_row(ROW).raw
    assert "user_reviews" not in raw
    assert "images" not in raw
    assert raw["title"] == "ELECTROMOTO"


def test_parse_row_tolerates_missing_values():
    place = parse_row({"title": "Sin datos", "review_rating": "", "review_count": "n/a"})
    assert place.place_id is None
    assert place.rating is None
    assert place.reviews_count is None
    assert place.emails == []
