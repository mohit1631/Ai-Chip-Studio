"""workshop / community sharing columns on projects

Revision ID: 0002_workshop_sharing
Revises: 0001_initial
Create Date: 2026-06-27

Adds the columns app/models.py's Project gained for the Workshop /
Community feature (publish, browse, fork): is_public, pdk, views, forks,
forked_from_id. Hand-written to match models.py -- same caveat as
0001_initial: confirm with `alembic check` against a real DB before
trusting it blindly.

Note: AICHIP_AUTO_CREATE_TABLES=true (the docker-compose default) makes
SQLAlchemy's Base.metadata.create_all() create these columns for free on
a brand-new database, since it creates the whole table from scratch. This
migration only matters -- but matters a lot -- on a database where the
projects table already exists (e.g. set AICHIP_AUTO_CREATE_TABLES=false
and run `alembic upgrade head` instead). create_all() never alters
existing tables, so without this migration every workshop endpoint will
fail at the DB with "column does not exist" on such a deployment.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_workshop_sharing"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("projects", sa.Column("pdk", sa.String(length=64), nullable=True))
    op.add_column(
        "projects",
        sa.Column("views", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "projects",
        sa.Column("forks", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "projects",
        sa.Column(
            "forked_from_id",
            sa.Integer(),
            sa.ForeignKey("projects.id"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("projects", "forked_from_id")
    op.drop_column("projects", "forks")
    op.drop_column("projects", "views")
    op.drop_column("projects", "pdk")
    op.drop_column("projects", "is_public")
