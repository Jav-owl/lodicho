"""estado_candidatura (inscrito | oficializado)

Revision ID: b7c41e9a2d10
Revises: 625edeb20616
Create Date: 2026-11-02

Las candidaturas existentes quedan como `inscrito` (la oficializacion es el
9 de noviembre); una carga posterior de la lista las va actualizando.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b7c41e9a2d10"
down_revision: Union[str, None] = "625edeb20616"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

VALORES = ("inscrito", "oficializado")


def upgrade() -> None:
    postgresql.ENUM(*VALORES, name="estado_candidatura").create(op.get_bind(), checkfirst=True)
    op.add_column(
        "candidaturas",
        sa.Column(
            "estado_candidatura",
            postgresql.ENUM(*VALORES, name="estado_candidatura", create_type=False),
            nullable=False,
            server_default="inscrito",
        ),
    )
    op.create_index("ix_candidaturas_estado_candidatura", "candidaturas", ["estado_candidatura"])


def downgrade() -> None:
    op.drop_index("ix_candidaturas_estado_candidatura", table_name="candidaturas")
    op.drop_column("candidaturas", "estado_candidatura")
    postgresql.ENUM(*VALORES, name="estado_candidatura").drop(op.get_bind(), checkfirst=True)
