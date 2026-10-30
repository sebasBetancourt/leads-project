from leadbot.agents.checks import OPT_OUT_LINE, check_message, ensure_opt_out, unsupported_claims
from leadbot.leads.status import Channel

SOURCES = "Catálogo por categorías. Operación 24/7. Rating 4.8 con 120 reseñas."


def test_opt_out_line_is_added_once():
    body = ensure_opt_out("Hola, vi su tienda.")
    assert body.endswith(OPT_OUT_LINE)
    assert ensure_opt_out(body) == body
    # El modelo a veces la escribe sin tilde o sin coma: no se duplica.
    written = "Hola.\nSi no te interesa respondeme NO y no te vuelvo a escribir"
    assert ensure_opt_out(written) == written


def test_clean_email_has_no_flags():
    flags = check_message(
        Channel.EMAIL, "Su catálogo en línea", "Hola, vimos sus 120 reseñas.", sources=SOURCES, max_chars=1200
    )
    assert flags == []


def test_email_needs_subject_and_no_emojis():
    flags = check_message(Channel.EMAIL, "", "Hola 🚀", sources=SOURCES, max_chars=1200)
    assert "falta el asunto" in flags
    assert "tiene emojis (no van en email)" in flags


def test_social_allows_one_emoji():
    assert check_message(Channel.WHATSAPP, None, "Hola 👋", sources=SOURCES, max_chars=700) == []
    assert check_message(Channel.WHATSAPP, None, "Hola 👋🚀", sources=SOURCES, max_chars=700)


def test_linkedin_length_limit():
    flags = check_message(Channel.LINKEDIN, None, "a" * 301, sources=SOURCES, max_chars=300)
    assert flags == ["muy largo: 301 caracteres (máximo 300)"]


def test_placeholders_are_flagged():
    flags = check_message(Channel.WHATSAPP, None, "Hola [Nombre], soy de BrainSol", sources=SOURCES, max_chars=700)
    assert flags and "marcadores" in flags[0]


def test_invented_numbers_are_flagged_but_sourced_ones_are_not():
    assert unsupported_claims("Aumentamos ventas un 40% y atendemos 24/7", SOURCES) == ["40%"]
    assert unsupported_claims("Con 120 reseñas y 4.8 de calificación", SOURCES) == []
    assert unsupported_claims("Ya ayudamos a 50 negocios", SOURCES) == ["50 negocios"]
