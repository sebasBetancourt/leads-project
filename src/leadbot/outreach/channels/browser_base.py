"""Base común de los canales que usan navegador (WhatsApp Web, Instagram, Facebook, LinkedIn).

Playwright con un perfil persistente por red en BROWSER_SESSIONS_DIR/<canal>: inicias sesión
una vez a mano (`leadbot login <canal>`) y el bot reutiliza esa sesión. Navegador visible,
ritmo humano, volumen bajo (config/limits.yaml). Sin técnicas de evasión de detección.
"""

from pathlib import Path

from leadbot.outreach.channels.base import ChannelAdapter


class BrowserChannel(ChannelAdapter):
    runs_in_docker = False

    def __init__(self, sessions_dir: str):
        self.profile_dir = Path(sessions_dir) / self.channel.value

    def login(self) -> None:
        """Abre el navegador en la página de login y espera a que inicies sesión a mano."""
        raise NotImplementedError("F4")
