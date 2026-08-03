"""performance reviews

Revision ID: a20966d7333b
Revises: 7c7bf3660cde
Create Date: 2026-08-03 23:09:23.368215
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = 'a20966d7333b'
down_revision = '7c7bf3660cde'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('performance_reviews',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('project_id', sa.String(length=36), nullable=False),
    sa.Column('strategy_id', sa.String(length=36), nullable=True),
    sa.Column('trade_count', sa.Integer(), nullable=False),
    sa.Column('realized_pnl', sa.Numeric(precision=20, scale=4), nullable=False),
    sa.Column('win_rate_pct', sa.Numeric(precision=10, scale=4), nullable=False),
    sa.Column('max_drawdown', sa.Numeric(precision=20, scale=4), nullable=False),
    sa.Column('accepted', sa.Boolean(), nullable=False),
    sa.Column('reasons', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('equity_curve', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('created_by', sa.String(length=120), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_performance_reviews_project_id_projects'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['strategy_id'], ['strategies.id'], name=op.f('fk_performance_reviews_strategy_id_strategies'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_performance_reviews'))
    )
    with op.batch_alter_table('performance_reviews', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_performance_reviews_project_id'), ['project_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('performance_reviews', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_performance_reviews_project_id'))

    op.drop_table('performance_reviews')
