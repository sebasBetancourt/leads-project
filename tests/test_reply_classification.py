"""Clasificación de correos entrantes: solo reglas, sin base de datos ni LLM."""

from pathlib import Path

import pytest

from leadbot.outreach.channels.mailparse import parse_email
from leadbot.outreach.replies import Kind, bounce_is_hard, classify, is_opt_out, strip_quoted

FIXTURES = Path(__file__).parent / "fixtures" / "mail"


def load(name: str):
    return parse_email(1, (FIXTURES / name).read_bytes())


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("opt_out_no.eml", Kind.OPT_OUT),
        ("interested_outlook_toppost.eml", Kind.REPLY),  # lo citado dice "respóndeme NO": no cuenta
        ("bounce_hard.eml", Kind.BOUNCE),
        ("bounce_soft.eml", Kind.SOFT_BOUNCE),
        ("auto_reply.eml", Kind.AUTO_REPLY),  # dice "respóndeme NO" pero es automática
    ],
)
def test_fixtures(name, kind):
    assert classify(load(name)) == kind


@pytest.mark.parametrize(
    "text",
    [
        "No",
        "no, gracias",
        "NO GRACIAS. Saludos",
        "Hola, no me interesa, gracias",
        "Por favor no me escriban más",
        "Quiero darme de baja",
        "Favor eliminarme de su lista",
        "unsubscribe",
        "Esto es spam",
        "No volver a escribir a este correo",
    ],
)
def test_opt_out_phrases(text):
    assert is_opt_out(text)


@pytest.mark.parametrize(
    "text",
    [
        "Me interesa, ¿cuánto cuesta?",
        "Hola, ¿puedes llamarme mañana?",
        "No tengo tiempo esta semana, escríbeme en diciembre",  # aplaza, no pide la baja
        "Nos contactó otra empresa antes",
        "Gracias por escribir. Lo reviso con mi socio.",
    ],
)
def test_normal_replies_are_not_opt_outs(text):
    assert not is_opt_out(text)


def test_subject_baja_from_list_unsubscribe_is_an_opt_out():
    assert is_opt_out("", subject="baja")


def test_strip_quoted_handles_gmail_outlook_and_prefixes():
    assert (
        strip_quoted("Sí.\n\nEl mar, 3 nov 2026 a las 10:00, Sebastian\n<s@b.co> escribió:\n> Si no te interesa")
        == "Sí."
    )
    assert strip_quoted("Dale\n\nDe: Sebastian\nEnviado: hoy\nNo me interesa") == "Dale"
    assert strip_quoted("Dale\n> no me interesa") == "Dale"
    assert strip_quoted("Dale\n\nOn Tue, Nov 3, 2026 Sebastian wrote:\n> no me interesa") == "Dale"


def test_bounce_severity():
    assert bounce_is_hard("Final-Recipient: rfc822; a@b.co\nStatus: 5.1.1")
    assert not bounce_is_hard("Action: delayed\nStatus: 4.2.2")
    assert bounce_is_hard("The email account that you tried to reach does not exist")
    assert not bounce_is_hard("Action: delayed\nmailbox is full")
