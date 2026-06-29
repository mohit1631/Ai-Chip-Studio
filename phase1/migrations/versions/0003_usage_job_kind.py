"""add 'job' value to usagekind enum

Revision ID: 0003_usage_job_kind
Revises: 0002_workshop_sharing
Create Date: 2026-06-28

Adds the 'job' value to the Postgres-native usagekind ENUM type, for the
"N jobs/month" free-trial cap added alongside the existing granular
ai_call/sim_minute metrics (see UsageKind in app/models.py, and
enforce_usage_limit() / record_usage() in app/deps.py).

Same AICHIP_AUTO_CREATE_TABLES=true caveat as 0002_workshop_sharing: a
brand-new database gets this for free via create_all(); an existing one
needs this migration run (`alembic upgrade head`) or every job-submission
endpoint will fail at the DB with "invalid input value for enum
usagekind" the first time it tries to insert a UsageRecord with kind='job'.

Postgres requires ALTER TYPE ... ADD VALUE to run outside an explicit
transaction block in older versions (fixed in PG 12+, but the
non-transactional ALTER TYPE is still the portable way to write this).
Alembic's autocommit_block() handles that here.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0003_usage_job_kind"
down_revision: Union[str, None] = "0002_workshop_sharing"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE usagekind ADD VALUE IF NOT EXISTS 'job'")


def downgrade() -> None:
    # Postgres has no ALTER TYPE ... DROP VALUE. Removing an enum value
    # cleanly requires rebuilding the type (create new type, migrate the
    # column, drop old type) -- not implemented here since downgrading
    # this specific change is unlikely to be needed; if you do need it,
    # write that rebuild by hand against your actual data first.
    raise NotImplementedError(
        "Postgres can't drop enum values directly. Rebuild the usagekind "
        "type by hand if you actually need to downgrade past this revision."
    )
