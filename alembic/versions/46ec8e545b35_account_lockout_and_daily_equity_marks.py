"""account lockout and daily equity marks

Revision ID: 46ec8e545b35
Revises: a20966d7333b
Create Date: 2026-08-04 11:12:01.429988
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = '46ec8e545b35'
down_revision = 'a20966d7333b'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('daily_equity_marks',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('date', sa.Date(), nullable=False),
    sa.Column('opening_equity', sa.Numeric(precision=20, scale=4), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_daily_equity_marks')),
    sa.UniqueConstraint('date', name='uq_daily_equity_marks_date')
    )
    with op.batch_alter_table('daily_equity_marks', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_daily_equity_marks_date'), ['date'], unique=False)

    with op.batch_alter_table('operators', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('failed_login_count', sa.Integer(), nullable=False, server_default='0')
        )
        batch_op.add_column(sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('operators', schema=None) as batch_op:
        batch_op.drop_column('locked_until')
        batch_op.drop_column('failed_login_count')

    with op.batch_alter_table('daily_equity_marks', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_daily_equity_marks_date'))

    op.drop_table('daily_equity_marks')
