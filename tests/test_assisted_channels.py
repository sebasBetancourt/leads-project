from leadbot.leads.status import Channel
from leadbot.outreach.channels.assisted import ASSISTED_CHANNELS


def link(channel: Channel, recipient: str, body: str = "Hola, ¿cómo vas?") -> str:
    return ASSISTED_CHANNELS[channel].chat_link(recipient, body)


def test_whatsapp_prefills_the_approved_message():
    assert link(Channel.WHATSAPP, "+573101234567") == (
        "https://wa.me/573101234567?text=Hola%2C%20%C2%BFc%C3%B3mo%20vas%3F"
    )


def test_instagram_and_facebook_open_the_dm():
    assert link(Channel.INSTAGRAM, "https://www.instagram.com/motos.bga/") == "https://ig.me/m/motos.bga"
    assert link(Channel.FACEBOOK, "https://www.facebook.com/MotosBGA") == "https://m.me/MotosBGA"


def test_facebook_without_username_opens_the_page():
    url = "https://www.facebook.com/profile.php?id=1000"
    assert link(Channel.FACEBOOK, url) == url


def test_email_is_not_assisted():
    assert Channel.EMAIL not in ASSISTED_CHANNELS
