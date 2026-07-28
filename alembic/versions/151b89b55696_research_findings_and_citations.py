"""research findings and citations

Revision ID: 151b89b55696
Revises: 7a2c4e91b3f0
Create Date: 2026-07-28 20:18:49.906235
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = '151b89b55696'
down_revision = '7a2c4e91b3f0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('research_findings',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('project_id', sa.String(length=36), nullable=False),
    sa.Column('question', sa.Text(), nullable=False),
    sa.Column('claim', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('reviewed_by', sa.String(length=120), nullable=True),
    sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_by', sa.String(length=120), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("status IN ('pending', 'accepted', 'rejected')", name=op.f('ck_research_findings_status_is_valid')),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_research_findings_project_id_projects'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_research_findings'))
    )
    with op.batch_alter_table('research_findings', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_research_findings_project_id'), ['project_id'], unique=False)
        batch_op.create_index('ix_research_findings_project_status', ['project_id', 'status'], unique=False)

    op.create_table('citations',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('finding_id', sa.String(length=36), nullable=False),
    sa.Column('url', sa.Text(), nullable=False),
    sa.Column('title', sa.String(length=500), nullable=False),
    sa.Column('quoted_text', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['finding_id'], ['research_findings.id'], name=op.f('fk_citations_finding_id_research_findings'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_citations'))
    )
    with op.batch_alter_table('citations', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_citations_finding_id'), ['finding_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('citations', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_citations_finding_id'))

    op.drop_table('citations')
    with op.batch_alter_table('research_findings', schema=None) as batch_op:
        batch_op.drop_index('ix_research_findings_project_status')
        batch_op.drop_index(batch_op.f('ix_research_findings_project_id'))

    op.drop_table('research_findings')
