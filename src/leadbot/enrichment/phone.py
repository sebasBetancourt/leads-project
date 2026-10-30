"""Normalización de teléfonos colombianos a E.164 (+57...)."""

import phonenumbers
from phonenumbers import NumberParseException, PhoneNumberType


def normalize_co(raw: str | None) -> str | None:
    """'310 123 4567' -> '+573101234567'. Devuelve None si no es un número válido."""
    if not raw:
        return None
    try:
        number = phonenumbers.parse(raw, "CO")
    except NumberParseException:
        return None
    if not phonenumbers.is_valid_number(number):
        return None
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)


def is_mobile(e164: str) -> bool:
    """Solo los celulares pueden tener WhatsApp: los fijos no se usan como canal de WhatsApp."""
    number = phonenumbers.parse(e164, None)
    return phonenumbers.number_type(number) in (
        PhoneNumberType.MOBILE,
        PhoneNumberType.FIXED_LINE_OR_MOBILE,
    )
