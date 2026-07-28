"""market data, strategy, backtest, risk assessment, order tables

Revision ID: 7a2c4e91b3f0
Revises: 3f9a21b6c8de
Create Date: 2026-07-28 16:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = '7a2c4e91b3f0'
down_revision = '3f9a21b6c8de'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('market_bars',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('symbol', sa.String(length=20), nullable=False),
    sa.Column('timeframe', sa.String(length=20), nullable=False),
    sa.Column('timestamp', sa.DateTime(timezone=True), nullable=False),
    sa.Column('open', sa.Numeric(precision=20, scale=4), nullable=False),
    sa.Column('high', sa.Numeric(precision=20, scale=4), nullable=False),
    sa.Column('low', sa.Numeric(precision=20, scale=4), nullable=False),
    sa.Column('close', sa.Numeric(precision=20, scale=4), nullable=False),
    sa.Column('volume', sa.BigInteger(), nullable=False),
    sa.Column('source', sa.String(length=40), nullable=False),
    sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_market_bars')),
    sa.UniqueConstraint('symbol', 'timeframe', 'timestamp', name='uq_market_bars_symbol_timeframe_ts')
    )
    with op.batch_alter_table('market_bars', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_market_bars_symbol'), ['symbol'], unique=False)

    op.create_table('strategies',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('project_id', sa.String(length=36), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('symbol', sa.String(length=20), nullable=False),
    sa.Column('timeframe', sa.String(length=20), nullable=False),
    sa.Column('fast_window', sa.Integer(), nullable=False),
    sa.Column('slow_window', sa.Integer(), nullable=False),
    sa.Column('minimum_out_of_sample_trades', sa.Integer(), nullable=False),
    sa.Column('created_by', sa.String(length=120), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_strategies_project_id_projects'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_strategies'))
    )
    with op.batch_alter_table('strategies', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_strategies_project_id'), ['project_id'], unique=False)

    op.create_table('backtests',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('project_id', sa.String(length=36), nullable=False),
    sa.Column('strategy_id', sa.String(length=36), nullable=False),
    sa.Column('start_date', sa.Date(), nullable=False),
    sa.Column('end_date', sa.Date(), nullable=False),
    sa.Column('total_return_pct', sa.Numeric(precision=10, scale=4), nullable=False),
    sa.Column('benchmark_return_pct', sa.Numeric(precision=10, scale=4), nullable=False),
    sa.Column('max_drawdown_pct', sa.Numeric(precision=10, scale=4), nullable=False),
    sa.Column('trade_count', sa.Integer(), nullable=False),
    sa.Column('out_of_sample_trade_count', sa.Integer(), nullable=False),
    sa.Column('accepted', sa.Boolean(), nullable=False),
    sa.Column('verdict_reason', sa.Text(), nullable=False),
    sa.Column('equity_curve', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('created_by', sa.String(length=120), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_backtests_project_id_projects'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['strategy_id'], ['strategies.id'], name=op.f('fk_backtests_strategy_id_strategies'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_backtests'))
    )
    with op.batch_alter_table('backtests', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_backtests_project_id'), ['project_id'], unique=False)

    op.create_table('risk_assessments',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('project_id', sa.String(length=36), nullable=False),
    sa.Column('backtest_id', sa.String(length=36), nullable=True),
    sa.Column('approved', sa.Boolean(), nullable=False),
    sa.Column('reasons', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('checked_limits', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('created_by', sa.String(length=120), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['backtest_id'], ['backtests.id'], name=op.f('fk_risk_assessments_backtest_id_backtests'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_risk_assessments_project_id_projects'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_risk_assessments'))
    )
    with op.batch_alter_table('risk_assessments', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_risk_assessments_project_id'), ['project_id'], unique=False)

    op.create_table('orders',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('project_id', sa.String(length=36), nullable=False),
    sa.Column('strategy_id', sa.String(length=36), nullable=True),
    sa.Column('broker_order_id', sa.String(length=64), nullable=False),
    sa.Column('symbol', sa.String(length=20), nullable=False),
    sa.Column('side', sa.String(length=10), nullable=False),
    sa.Column('qty', sa.Numeric(precision=20, scale=4), nullable=False),
    sa.Column('order_type', sa.String(length=20), nullable=False),
    sa.Column('time_in_force', sa.String(length=20), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('filled_avg_price', sa.Numeric(precision=20, scale=4), nullable=True),
    sa.Column('filled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('submitted_by', sa.String(length=120), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_orders_project_id_projects'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['strategy_id'], ['strategies.id'], name=op.f('fk_orders_strategy_id_strategies'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_orders'))
    )
    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_orders_broker_order_id'), ['broker_order_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_orders_project_id'), ['project_id'], unique=False)

    op.create_table('mock_fills',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('symbol', sa.String(length=20), nullable=False),
    sa.Column('side', sa.String(length=10), nullable=False),
    sa.Column('qty', sa.Numeric(precision=20, scale=4), nullable=False),
    sa.Column('price', sa.Numeric(precision=20, scale=4), nullable=False),
    sa.Column('filled_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_mock_fills'))
    )
    with op.batch_alter_table('mock_fills', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_mock_fills_symbol'), ['symbol'], unique=False)

    approval_kinds = sa.table(
        'approval_kinds',
        sa.column('code', sa.String),
        sa.column('description', sa.Text),
    )
    op.bulk_insert(approval_kinds, [
        {
            'code': 'enter:PAPER_TRADING',
            'description': 'Human approval required to move a project into live paper trading.',
        },
    ])


def downgrade() -> None:
    op.execute("DELETE FROM approval_kinds WHERE code = 'enter:PAPER_TRADING'")

    with op.batch_alter_table('mock_fills', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_mock_fills_symbol'))
    op.drop_table('mock_fills')

    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_orders_project_id'))
        batch_op.drop_index(batch_op.f('ix_orders_broker_order_id'))
    op.drop_table('orders')

    with op.batch_alter_table('risk_assessments', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_risk_assessments_project_id'))
    op.drop_table('risk_assessments')

    with op.batch_alter_table('backtests', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_backtests_project_id'))
    op.drop_table('backtests')

    with op.batch_alter_table('strategies', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_strategies_project_id'))
    op.drop_table('strategies')

    with op.batch_alter_table('market_bars', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_market_bars_symbol'))
    op.drop_table('market_bars')
