"""F2: agente y aprobación en Sheets

- research: tokens y costo de la IA por lead.
- messages: texto original del agente, alertas de revisión y decisión tomada en Sheets.
- jobs: dedup_key para no encolar dos veces el mismo trabajo mientras esté vivo.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-30 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0002'
down_revision: str | None = '0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('research', sa.Column('tokens', sa.Integer(), server_default='0', nullable=False))
    op.add_column('research', sa.Column('cost_usd', sa.Double(), server_default='0', nullable=False))

    op.add_column('messages', sa.Column('original_body', sa.Text(), nullable=True))
    op.add_column('messages', sa.Column('review_flags', sa.JSON(), server_default='[]', nullable=False))
    op.add_column('messages', sa.Column('decision', sa.String(length=16), nullable=True))
    op.create_index('ix_messages_company_status', 'messages', ['company_id', 'status'], unique=False)

    op.add_column('jobs', sa.Column('dedup_key', sa.String(length=128), nullable=True))
    op.create_index(
        'uq_jobs_alive', 'jobs', ['kind', 'dedup_key'], unique=True,
        postgresql_where=sa.text("status IN ('pending', 'running')"),
    )


def downgrade() -> None:
    op.drop_index('uq_jobs_alive', table_name='jobs')
    op.drop_column('jobs', 'dedup_key')
    op.drop_index('ix_messages_company_status', table_name='messages')
    op.drop_column('messages', 'decision')
    op.drop_column('messages', 'review_flags')
    op.drop_column('messages', 'original_body')
    op.drop_column('research', 'cost_usd')
    op.drop_column('research', 'tokens')
