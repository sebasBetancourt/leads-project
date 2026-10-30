"""Alembic: usa los modelos de leads/models.py y la URL de DATABASE_URL."""

from alembic import context

from leadbot.core.db import Base, engine
from leadbot.leads import models  # noqa: F401  registra las tablas en Base.metadata


def run_migrations() -> None:
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


run_migrations()
