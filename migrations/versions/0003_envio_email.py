"""F3: envío de email, respuestas y control en caliente

- messages: Message-ID del email (se guarda antes de enviar) y cuándo respondió el destinatario.
- contact_channels: marca de contacto inválido (rebote).
- runtime_flags: pausas que se cambian sin reiniciar el worker.
- mail_cursor: último UID leído de la bandeja de entrada.
- worker_heartbeats: última ejecución de cada tarea del worker.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-30 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0003'
down_revision: str | None = '0002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('messages', sa.Column('external_id', sa.String(length=255), nullable=True))
    op.add_column('messages', sa.Column('reply_at', sa.DateTime(), nullable=True))
    op.create_unique_constraint('uq_messages_external_id', 'messages', ['external_id'])
    op.add_column('contact_channels', sa.Column('invalid', sa.Boolean(), server_default='false', nullable=False))

    op.create_table(
        'runtime_flags',
        sa.Column('key', sa.String(length=64), nullable=False),
        sa.Column('value', sa.String(length=255), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('key'),
    )
    op.create_table(
        'mail_cursor',
        sa.Column('folder', sa.String(length=255), nullable=False),
        sa.Column('uidvalidity', sa.BigInteger(), nullable=True),
        sa.Column('last_uid', sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint('folder'),
    )
    op.create_table(
        'worker_heartbeats',
        sa.Column('task', sa.String(length=64), nullable=False),
        sa.Column('last_run', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('ok', sa.Boolean(), nullable=False),
        sa.Column('detail', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('task'),
    )


def downgrade() -> None:
    op.drop_table('worker_heartbeats')
    op.drop_table('mail_cursor')
    op.drop_table('runtime_flags')
    op.drop_column('contact_channels', 'invalid')
    op.drop_constraint('uq_messages_external_id', 'messages', type_='unique')
    op.drop_column('messages', 'reply_at')
    op.drop_column('messages', 'external_id')
