"""knowledge tests, hypotheses, generated strategy code

Revision ID: 7c7bf3660cde
Revises: 151b89b55696
Create Date: 2026-07-30 14:36:16.270208
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = '7c7bf3660cde'
down_revision = '151b89b55696'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('hypotheses',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('project_id', sa.String(length=36), nullable=False),
    sa.Column('statement', sa.Text(), nullable=False),
    sa.Column('rationale', sa.Text(), nullable=False),
    sa.Column('cited_finding_ids', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('created_by', sa.String(length=120), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_hypotheses_project_id_projects'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_hypotheses'))
    )
    with op.batch_alter_table('hypotheses', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_hypotheses_project_id'), ['project_id'], unique=False)

    op.create_table('knowledge_tests',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('project_id', sa.String(length=36), nullable=False),
    sa.Column('question', sa.Text(), nullable=False),
    sa.Column('verdict', sa.String(length=20), nullable=False),
    sa.Column('reasoning', sa.Text(), nullable=False),
    sa.Column('cited_finding_ids', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('created_by', sa.String(length=120), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("verdict IN ('supported', 'not_supported', 'contradicted')", name=op.f('ck_knowledge_tests_verdict_is_valid')),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_knowledge_tests_project_id_projects'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_knowledge_tests'))
    )
    with op.batch_alter_table('knowledge_tests', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_knowledge_tests_project_id'), ['project_id'], unique=False)

    op.create_table('generated_strategy_code',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('project_id', sa.String(length=36), nullable=False),
    sa.Column('base_strategy_id', sa.String(length=36), nullable=False),
    sa.Column('fast_window', sa.Integer(), nullable=False),
    sa.Column('slow_window', sa.Integer(), nullable=False),
    sa.Column('minimum_out_of_sample_trades', sa.Integer(), nullable=False),
    sa.Column('rationale', sa.Text(), nullable=False),
    sa.Column('validated', sa.Boolean(), nullable=True),
    sa.Column('validation_reasons', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('produced_strategy_id', sa.String(length=36), nullable=True),
    sa.Column('created_by', sa.String(length=120), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['base_strategy_id'], ['strategies.id'], name=op.f('fk_generated_strategy_code_base_strategy_id_strategies'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['produced_strategy_id'], ['strategies.id'], name=op.f('fk_generated_strategy_code_produced_strategy_id_strategies'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_generated_strategy_code_project_id_projects'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_generated_strategy_code'))
    )
    with op.batch_alter_table('generated_strategy_code', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_generated_strategy_code_project_id'), ['project_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('generated_strategy_code', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_generated_strategy_code_project_id'))

    op.drop_table('generated_strategy_code')
    with op.batch_alter_table('knowledge_tests', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_knowledge_tests_project_id'))

    op.drop_table('knowledge_tests')
    with op.batch_alter_table('hypotheses', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_hypotheses_project_id'))

    op.drop_table('hypotheses')
