from leadbot.enrichment.phone import is_mobile, normalize_co


def test_normalizes_colombian_mobile_formats():
    for raw in ["310 123 4567", "+57 310 1234567", "(+57) 310-123-4567", "573101234567"]:
        assert normalize_co(raw) == "+573101234567"


def test_landline_is_not_mobile():
    landline = normalize_co("(601) 7654321")  # fijo Bogotá
    assert landline is not None
    assert not is_mobile(landline)
    assert is_mobile("+573101234567")


def test_invalid_numbers():
    assert normalize_co(None) is None
    assert normalize_co("") is None
    assert normalize_co("123") is None
    assert normalize_co("no tiene") is None
