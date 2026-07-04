"""messages_wamid_idempotency

Revision ID: c919bd395589
Revises: b13cc9253686
Create Date: 2026-07-04 09:42:42.010506

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c919bd395589'
down_revision: Union[str, None] = 'b13cc9253686'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('messages', sa.Column('wamid', sa.String(length=100), nullable=True))
    op.create_index('uq_messages_wamid', 'messages', ['wamid'], unique=True)


def downgrade() -> None:
    op.drop_index('uq_messages_wamid', table_name='messages')
    op.drop_column('messages', 'wamid')
