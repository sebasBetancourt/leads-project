"""Aplica las migraciones de Alembic desde código (leadbot migrate, y al arrancar el worker)."""

from alembic import command
from alembic.config import Config

from leadbot.core.config import ROOT_DIR


def upgrade() -> None:
    command.upgrade(Config(str(ROOT_DIR / "alembic.ini")), "head")
