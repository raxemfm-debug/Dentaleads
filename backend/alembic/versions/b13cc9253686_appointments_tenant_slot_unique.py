"""appointments_tenant_slot_unique

Revision ID: b13cc9253686
Revises: 7c04d256ea5b
Create Date: 2026-07-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b13cc9253686'
down_revision: Union[str, None] = '7c04d256ea5b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "uq_appointments_tenant_slot",
        "appointments",
        ["tenant_id", "scheduled_at"],
        unique=True,
        postgresql_where=sa.text("status NOT IN ('cancelled', 'no_show')"),
    )


def downgrade() -> None:
    op.drop_index("uq_appointments_tenant_slot", table_name="appointments")
