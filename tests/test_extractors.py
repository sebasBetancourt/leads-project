from leadbot.enrichment.extractors import (
    ExtractedContacts,
    clean_email,
    extract_contacts,
    keep_relevant,
    social_profile,
)

HTML = """
<html><body>
  <p>Escríbenos a ventas@motosbga.com o a soporte@ejemplo.com</p>
  <img src="logo@2x.png">
  <a href="mailto:Gerencia@MotosBGA.com?subject=Hola">Gerencia</a>
  <a href="tel:+57 310 123 4567">Llámanos</a>
  <a href="https://wa.me/573101234567?text=hola">WhatsApp</a>
  <a href="https://api.whatsapp.com/send?phone=573209876543">WhatsApp 2</a>
  <a href="https://www.instagram.com/motos.bga/?hl=es">IG</a>
  <a href="https://www.instagram.com/p/C123abc/">Una publicación</a>
  <a href="https://www.facebook.com/MotosBGA/">FB</a>
  <a href="https://www.facebook.com/sharer/sharer.php?u=x">Compartir</a>
  <a href="https://co.linkedin.com/company/motos-bga">LinkedIn</a>
</body></html>
"""


def test_extracts_all_channels():
    found = extract_contacts(HTML, "https://motosbga.com")
    assert found.emails == {"ventas@motosbga.com", "gerencia@motosbga.com"}  # soporte@ejemplo.com es plantilla
    assert found.phones == {"+573101234567"}
    assert found.whatsapp == {"+573101234567", "+573209876543"}
    assert found.instagram == {"https://www.instagram.com/motos.bga/"}
    assert found.facebook == {"https://www.facebook.com/MotosBGA"}


def test_rejects_template_and_image_emails():
    assert clean_email("user@example.com") is None
    assert clean_email("logo@2x.png") is None
    assert clean_email("abc@sentry.wixpress.com") is None
    assert clean_email(" Ventas@Tienda.co. ") == "ventas@tienda.co"


def test_social_profile_ignores_posts_and_share_buttons():
    assert social_profile("https://www.instagram.com/reel/xyz/") is None
    assert social_profile("https://facebook.com/sharer.php?u=x") is None
    assert social_profile("https://www.facebook.com/profile.php?id=1000") == (
        "facebook",
        "https://www.facebook.com/profile.php?id=1000",
    )
    assert social_profile("https://www.linkedin.com/company/brainsol/about") == (
        "linkedin",
        "https://www.linkedin.com/company/brainsol/",
    )


def test_rejects_spanish_placeholders():
    assert clean_email("contacto@ejemplo.com") is None
    assert clean_email("ventas@mail.com") is None
    assert clean_email("ventas@gmail.com") == "ventas@gmail.com"


def test_keep_relevant_drops_agency_and_brand_contacts():
    found = ExtractedContacts(
        emails={"ventas@losladeros.com", "cliente@gurusoluciones.com", "losladeros@gmail.com"},
        instagram={"https://www.instagram.com/loslaredossas/", "https://www.instagram.com/global.digital.cooperative/"},
        facebook={"https://www.facebook.com/publicired"},
    )
    kept = keep_relevant(found, name="LOS LAREDOS SAS", website="https://www.losladeros.com")
    assert kept.emails == {"ventas@losladeros.com", "losladeros@gmail.com"}
    assert kept.instagram == {"https://www.instagram.com/loslaredossas/"}
    assert kept.facebook == {"https://www.facebook.com/publicired"}  # una sola cuenta: no hay con qué comparar
