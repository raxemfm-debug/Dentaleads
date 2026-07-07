"""messages_sequence_ordering

Revision ID: 7cbaca3d97ec
Revises: c919bd395589
Create Date: 2026-07-07 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '7cbaca3d97ec'
down_revision: Union[str, None] = 'c919bd395589'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('messages', sa.Column('sequence', sa.Integer(), nullable=True))

    # Backfill existing rows (DT-001): assign a stable per-conversation ordering from
    # the order they were actually created in. created_at ties (rows persisted in the
    # same transaction) are broken by id only to make the backfill deterministic — the
    # true relative order of those old rows is already unrecoverable.
    op.execute(
        """
        WITH ranked AS (
            SELECT id, ROW_NUMBER() OVER (
                PARTITION BY conversation_id ORDER BY created_at, id
            ) - 1 AS rn
            FROM messages
        )
        UPDATE messages
        SET sequence = ranked.rn
        FROM ranked
        WHERE messages.id = ranked.id
        """
    )

    op.alter_column('messages', 'sequence', nullable=False)
    op.drop_index('ix_messages_conversation_id', table_name='messages')
    op.create_index(
        'ix_messages_conversation_id_sequence', 'messages', ['conversation_id', 'sequence']
    )


def downgrade() -> None:
    op.drop_index('ix_messages_conversation_id_sequence', table_name='messages')
    op.create_index('ix_messages_conversation_id', 'messages', ['conversation_id'])
    op.drop_column('messages', 'sequence')
