"""messages_sequence_unique_dt006

DT-006: sequence was assigned in Python as max()+1 with no locking, so two webhooks
racing on the same conversation (e.g. two genuinely separate inbound messages landing
a few seconds apart, not just a Meta retry) could both read the same max and both
insert the same sequence — observed in production on conversation
cb32ffe3-437d-492f-97bc-d21c35793d36, sequences 72 and 73 duplicated. The race itself
is fixed in application code (with_for_update() in conversation._get_or_create_conversation);
this migration is the DB-level backstop plus the one-time cleanup of the rows that
already collided.

Repair first, then constrain: a UNIQUE index cannot be created while duplicates exist.
The renumber below is scoped to only the conversation_ids that actually have a
duplicate (found live via GROUP BY ... HAVING count(*) > 1 — one conversation, as of
this writing), so conversations with no collision are never touched. It reuses the
exact ROW_NUMBER() OVER (PARTITION BY conversation_id ORDER BY ...) approach already
used once in this codebase for the original sequence backfill (7cbaca3d97ec), with
`created_at` as the primary sort key (reliably separates the two racing transactions —
Postgres now() is stable *within* one transaction but differs *across* them, unlike the
DT-001 caveat about ordering within a single call's own multi-row turn), the original
`sequence` as a secondary key (correctly preserves intra-transaction turn order, e.g.
tool_use -> tool_result -> assistant, since that part was never wrong), and `id` as a
last-resort tiebreak for determinism. No message content, role, or wamid is touched —
only the losing group's ordering key shifts to a later, still-correct position.

Doing the repair and the constraint in one revision is deliberate: if the renumber
above ever misses a case, CREATE UNIQUE INDEX fails immediately inside this same
migration transaction instead of silently leaving a partial fix in place.

Revision ID: a9b593a43546
Revises: 787ddddd40c6
Create Date: 2026-07-10 19:29:39.378606

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a9b593a43546'
down_revision: Union[str, None] = '787ddddd40c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        WITH affected AS (
            SELECT conversation_id FROM messages
            GROUP BY conversation_id, sequence
            HAVING count(*) > 1
        ),
        ranked AS (
            SELECT id, ROW_NUMBER() OVER (
                PARTITION BY conversation_id ORDER BY created_at, sequence, id
            ) - 1 AS new_sequence
            FROM messages
            WHERE conversation_id IN (SELECT conversation_id FROM affected)
        )
        UPDATE messages
        SET sequence = ranked.new_sequence
        FROM ranked
        WHERE messages.id = ranked.id
        """
    )

    op.drop_index("ix_messages_conversation_id_sequence", table_name="messages")
    op.create_index(
        "uq_messages_conversation_id_sequence",
        "messages",
        ["conversation_id", "sequence"],
        unique=True,
    )


def downgrade() -> None:
    # The renumber above is not reversible: once two colliding rows are told apart by
    # created_at/sequence/id, which one "should" have kept the original number is not
    # recoverable information (same limitation the original 7cbaca3d97ec backfill
    # already accepted). Only the constraint half is undone here.
    op.drop_index("uq_messages_conversation_id_sequence", table_name="messages")
    op.create_index(
        "ix_messages_conversation_id_sequence", "messages", ["conversation_id", "sequence"]
    )
