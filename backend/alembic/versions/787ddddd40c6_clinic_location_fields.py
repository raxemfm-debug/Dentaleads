"""clinic_location_fields

Revision ID: 787ddddd40c6
Revises: 7cbaca3d97ec
Create Date: 2026-07-10 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '787ddddd40c6'
down_revision: Union[str, None] = '7cbaca3d97ec'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('clinics', sa.Column('address_reference', sa.Text(), nullable=True))
    op.add_column('clinics', sa.Column('maps_url', sa.String(length=500), nullable=True))
    op.add_column('clinics', sa.Column('contact_phone', sa.String(length=50), nullable=True))


def downgrade() -> None:
    op.drop_column('clinics', 'contact_phone')
    op.drop_column('clinics', 'maps_url')
    op.drop_column('clinics', 'address_reference')
