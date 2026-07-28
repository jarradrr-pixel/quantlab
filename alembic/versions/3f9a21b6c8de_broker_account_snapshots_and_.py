"""broker account snapshots and reconciliation runs

Revision ID: 3f9a21b6c8de
Revises: 01ec1780ac79
Create Date: 2026-07-28 13:30:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = '3f9a21b6c8de'
down_revision = '01ec1780ac79'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('broker_account_snapshots',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('actor', sa.String(length=120), nullable=False),
    sa.Column('account_id', sa.String(length=64), nullable=False),
    sa.Column('account_status', sa.String(length=40), nullable=False),
    sa.Column('is_paper', sa.Boolean(), nullable=False),
    sa.Column('currency', sa.String(length=10), nullable=False),
    sa.Column('cash', sa.Numeric(precision=20, scale=4), nullable=False),
    sa.Column('portfolio_value', sa.Numeric(precision=20, scale=4), nullable=False),
    sa.Column('buying_power', sa.Numeric(precision=20, scale=4), nullable=False),
    sa.Column('pattern_day_trader', sa.Boolean(), nullable=False),
    sa.Column('trading_blocked', sa.Boolean(), nullable=False),
    sa.Column('raw_response', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_broker_account_snapshots'))
    )
    with op.batch_alter_table('broker_account_snapshots', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_broker_account_snapshots_created_at'), ['created_at'], unique=False)

    op.create_table('broker_reconciliation_runs',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('actor', sa.String(length=120), nullable=False),
    sa.Column('snapshot_id', sa.String(length=36), nullable=True),
    sa.Column('open_position_count', sa.Integer(), nullable=False),
    sa.Column('open_order_count', sa.Integer(), nullable=False),
    sa.Column('untracked_position_count', sa.Integer(), nullable=False),
    sa.Column('untracked_order_count', sa.Integer(), nullable=False),
    sa.Column('findings', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['snapshot_id'], ['broker_account_snapshots.id'], name=op.f('fk_broker_reconciliation_runs_snapshot_id_broker_account_snapshots'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_broker_reconciliation_runs'))
    )
    with op.batch_alter_table('broker_reconciliation_runs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_broker_reconciliation_runs_created_at'), ['created_at'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('broker_reconciliation_runs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_broker_reconciliation_runs_created_at'))

    op.drop_table('broker_reconciliation_runs')
    with op.batch_alter_table('broker_account_snapshots', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_broker_account_snapshots_created_at'))

    op.drop_table('broker_account_snapshots')
