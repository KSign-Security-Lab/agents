"""initial schema

Revision ID: 63c60e8b2fa6
Revises: 
Create Date: 2026-08-19 09:40:20.498501
"""
from __future__ import annotations

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '63c60e8b2fa6'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # pgvector must exist before any vector/sparsevec column is created.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table('topics',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('slug', sa.String(length=180), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('embedding', pgvector.sqlalchemy.vector.VECTOR(dim=1024), nullable=True),
    sa.Column('doc_count', sa.Integer(), nullable=False),
    sa.Column('created_by', sa.String(length=16), nullable=False),
    sa.Column('merged_into_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['merged_into_id'], ['topics.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('slug')
    )
    op.create_table('users',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('role', sa.Enum('admin', 'member', name='user_role'), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('external_id', sa.String(length=255), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('external_id')
    )
    op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=True)
    op.create_table('documents',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('filename', sa.String(length=512), nullable=False),
    sa.Column('mime', sa.String(length=160), nullable=False),
    sa.Column('size_bytes', sa.BigInteger(), nullable=False),
    sa.Column('source_kind', sa.Enum('pdf', 'scanned', 'office', 'hwp', 'image', 'text', 'audio', 'video', name='source_kind'), nullable=False),
    sa.Column('status', sa.Enum('pending', 'converting', 'extracting', 'transcribing', 'chunking', 'embedding', 'categorizing', 'ready', 'failed', name='doc_status'), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('key_original', sa.String(length=512), nullable=False),
    sa.Column('key_pdf', sa.String(length=512), nullable=True),
    sa.Column('key_media', sa.String(length=512), nullable=True),
    sa.Column('page_count', sa.Integer(), nullable=True),
    sa.Column('duration_ms', sa.Integer(), nullable=True),
    sa.Column('summary', sa.Text(), nullable=True),
    sa.Column('key_entities', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('suggested_questions', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('uploader_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['uploader_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('sha256', name='uq_documents_sha256')
    )
    op.create_index(op.f('ix_documents_sha256'), 'documents', ['sha256'], unique=False)
    op.create_index(op.f('ix_documents_status'), 'documents', ['status'], unique=False)
    op.create_index('ix_documents_status_created', 'documents', ['status', 'created_at'], unique=False)
    op.create_table('topic_aliases',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('topic_id', sa.UUID(), nullable=False),
    sa.Column('alias', sa.String(length=160), nullable=False),
    sa.ForeignKeyConstraint(['topic_id'], ['topics.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('topic_id', 'alias', name='uq_topic_alias')
    )
    op.create_index(op.f('ix_topic_aliases_topic_id'), 'topic_aliases', ['topic_id'], unique=False)
    op.create_table('document_elements',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('document_id', sa.UUID(), nullable=False),
    sa.Column('page_no', sa.Integer(), nullable=True),
    sa.Column('kind', sa.Enum('heading', 'paragraph', 'table', 'figure', 'list_item', 'caption', 'transcript', name='element_kind'), nullable=False),
    sa.Column('level', sa.Integer(), nullable=True),
    sa.Column('reading_order', sa.Integer(), nullable=False),
    sa.Column('text', sa.Text(), nullable=True),
    sa.Column('bbox', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('table_json', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_document_elements_document_id'), 'document_elements', ['document_id'], unique=False)
    op.create_index('ix_elements_doc_order', 'document_elements', ['document_id', 'reading_order'], unique=False)
    op.create_table('document_pages',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('document_id', sa.UUID(), nullable=False),
    sa.Column('page_no', sa.Integer(), nullable=False),
    sa.Column('width', sa.Float(), nullable=False),
    sa.Column('height', sa.Float(), nullable=False),
    sa.Column('rotation', sa.Integer(), nullable=False),
    sa.Column('has_text_layer', sa.Boolean(), nullable=False),
    sa.Column('key_thumb', sa.String(length=512), nullable=True),
    sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('document_id', 'page_no', name='uq_page_per_doc')
    )
    op.create_table('document_topics',
    sa.Column('document_id', sa.UUID(), nullable=False),
    sa.Column('topic_id', sa.UUID(), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=False),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['topic_id'], ['topics.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('document_id', 'topic_id')
    )
    op.create_table('ingest_jobs',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('document_id', sa.UUID(), nullable=False),
    sa.Column('stage', sa.String(length=40), nullable=False),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('document_id', 'stage', name='uq_job_stage')
    )
    op.create_index(op.f('ix_ingest_jobs_document_id'), 'ingest_jobs', ['document_id'], unique=False)
    op.create_table('channels',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('active_leaf_id', sa.UUID(), nullable=True),
    sa.Column('archived', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name', name='uq_channels_name')
    )
    op.create_table('chunks',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('document_id', sa.UUID(), nullable=False),
    sa.Column('ord', sa.Integer(), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('heading_path', sa.Text(), nullable=True),
    sa.Column('token_count', sa.Integer(), nullable=False),
    sa.Column('page_from', sa.Integer(), nullable=True),
    sa.Column('page_to', sa.Integer(), nullable=True),
    sa.Column('t_start_ms', sa.Integer(), nullable=True),
    sa.Column('t_end_ms', sa.Integer(), nullable=True),
    sa.Column('is_table', sa.Boolean(), nullable=False),
    sa.Column('element_id', sa.BigInteger(), nullable=True),
    sa.Column('embedding', pgvector.sqlalchemy.vector.VECTOR(dim=1024), nullable=True),
    sa.Column('sparse', pgvector.sqlalchemy.sparsevec.SPARSEVEC(dim=250002), nullable=True),
    sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['element_id'], ['document_elements.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('document_id', 'ord', name='uq_chunk_per_doc')
    )
    op.create_index('ix_chunks_doc_page', 'chunks', ['document_id', 'page_from'], unique=False)
    op.create_index(op.f('ix_chunks_document_id'), 'chunks', ['document_id'], unique=False)
    op.create_table('messages',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('channel_id', sa.UUID(), nullable=False),
    sa.Column('parent_id', sa.UUID(), nullable=True),
    sa.Column('branch_root_id', sa.UUID(), nullable=True),
    sa.Column('role', sa.Enum('user', 'assistant', 'system', name='message_role'), nullable=False),
    sa.Column('author_id', sa.UUID(), nullable=True),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('status', sa.Enum('queued', 'running', 'complete', 'failed', 'cancelled', name='message_status'), nullable=False),
    sa.Column('model', sa.String(length=200), nullable=True),
    sa.Column('token_usage', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['author_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['parent_id'], ['messages.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['channel_id'], ['channels.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_messages_branch_root_id'), 'messages', ['branch_root_id'], unique=False)
    op.create_index(op.f('ix_messages_parent_id'), 'messages', ['parent_id'], unique=False)
    op.create_index('ix_messages_channel_created', 'messages', ['channel_id', 'created_at'], unique=False)
    op.create_index(op.f('ix_messages_channel_id'), 'messages', ['channel_id'], unique=False)
    # Deferred: channels.active_leaf_id references a table that doesn't exist
    # yet at channels' own create_table time. use_alter=True on that column's
    # ForeignKeyConstraint only suppresses it from the inline CREATE TABLE —
    # it does not, by itself, schedule this ALTER TABLE, so it has to be added
    # explicitly once messages exists.
    op.create_foreign_key('channels_active_leaf_id_fkey', 'channels', 'messages',
                          ['active_leaf_id'], ['id'], ondelete='SET NULL')
    op.create_table('channel_documents',
    sa.Column('channel_id', sa.UUID(), nullable=False),
    sa.Column('document_id', sa.UUID(), nullable=False),
    sa.Column('added_by', sa.UUID(), nullable=True),
    sa.ForeignKeyConstraint(['added_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['channel_id'], ['channels.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('channel_id', 'document_id')
    )
    op.create_table('agent_runs',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('message_id', sa.UUID(), nullable=False),
    sa.Column('graph', sa.String(length=80), nullable=False),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('latency_ms', sa.Integer(), nullable=True),
    sa.Column('rejected_citations', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['message_id'], ['messages.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_agent_runs_message_id'), 'agent_runs', ['message_id'], unique=False)
    op.create_table('chunk_spans',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('chunk_id', sa.BigInteger(), nullable=False),
    sa.Column('text_start', sa.Integer(), nullable=False),
    sa.Column('text_end', sa.Integer(), nullable=False),
    sa.Column('page_no', sa.Integer(), nullable=True),
    sa.Column('bbox', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('t_start_ms', sa.Integer(), nullable=True),
    sa.Column('t_end_ms', sa.Integer(), nullable=True),
    sa.CheckConstraint('text_end >= text_start', name='ck_span_range'),
    sa.ForeignKeyConstraint(['chunk_id'], ['chunks.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_chunk_spans_chunk_id'), 'chunk_spans', ['chunk_id'], unique=False)
    op.create_index('ix_spans_chunk_range', 'chunk_spans', ['chunk_id', 'text_start'], unique=False)
    op.create_table('citations',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('message_id', sa.UUID(), nullable=False),
    sa.Column('idx', sa.Integer(), nullable=False),
    sa.Column('document_id', sa.UUID(), nullable=False),
    sa.Column('chunk_id', sa.BigInteger(), nullable=True),
    sa.Column('page_no', sa.Integer(), nullable=True),
    sa.Column('rects', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('t_start_ms', sa.Integer(), nullable=True),
    sa.Column('t_end_ms', sa.Integer(), nullable=True),
    sa.Column('snippet', sa.Text(), nullable=False),
    sa.Column('heading_path', sa.Text(), nullable=True),
    sa.Column('score', sa.Float(), nullable=True),
    sa.Column('out_of_scope', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['chunk_id'], ['chunks.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['message_id'], ['messages.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('message_id', 'idx', name='uq_citation_idx')
    )
    op.create_index(op.f('ix_citations_message_id'), 'citations', ['message_id'], unique=False)
    op.create_table('agent_steps',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('run_id', sa.UUID(), nullable=False),
    sa.Column('ord', sa.Integer(), nullable=False),
    sa.Column('node', sa.String(length=80), nullable=False),
    sa.Column('label', sa.String(length=300), nullable=True),
    sa.Column('input_json', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('output_json', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('latency_ms', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['run_id'], ['agent_runs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_agent_steps_run_id'), 'agent_steps', ['run_id'], unique=False)
    # ### end Alembic commands ###

    # ------------------------------------------------------------------ ANN
    # Hybrid retrieval searches the dense column by cosine distance and the
    # sparse column by (negative) inner product, so each needs its own operator
    # class. Autogenerate cannot emit these because it does not know which
    # distance a given vector column will be searched with.
    op.execute(
        "CREATE INDEX ix_chunks_embedding_hnsw ON chunks "
        "USING hnsw (embedding vector_cosine_ops) WITH (m = 24, ef_construction = 128)"
    )
    op.execute(
        "CREATE INDEX ix_chunks_sparse_hnsw ON chunks "
        "USING hnsw (sparse sparsevec_ip_ops) WITH (m = 24, ef_construction = 128)"
    )
    # Topic de-duplication compares a proposed tag against every existing topic.
    op.execute(
        "CREATE INDEX ix_topics_embedding_hnsw ON topics "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_topics_embedding_hnsw")
    op.execute("DROP INDEX IF EXISTS ix_chunks_sparse_hnsw")
    op.execute("DROP INDEX IF EXISTS ix_chunks_embedding_hnsw")

    op.drop_index(op.f('ix_agent_steps_run_id'), table_name='agent_steps')
    op.drop_table('agent_steps')
    op.drop_index(op.f('ix_citations_message_id'), table_name='citations')
    op.drop_table('citations')
    op.drop_index('ix_spans_chunk_range', table_name='chunk_spans')
    op.drop_index(op.f('ix_chunk_spans_chunk_id'), table_name='chunk_spans')
    op.drop_table('chunk_spans')
    op.drop_index(op.f('ix_agent_runs_message_id'), table_name='agent_runs')
    op.drop_table('agent_runs')
    op.drop_table('channel_documents')
    op.drop_constraint('channels_active_leaf_id_fkey', 'channels', type_='foreignkey')
    op.drop_index(op.f('ix_messages_channel_id'), table_name='messages')
    op.drop_index('ix_messages_channel_created', table_name='messages')
    op.drop_index(op.f('ix_messages_parent_id'), table_name='messages')
    op.drop_index(op.f('ix_messages_branch_root_id'), table_name='messages')
    op.drop_table('messages')
    op.drop_index(op.f('ix_chunks_document_id'), table_name='chunks')
    op.drop_index('ix_chunks_doc_page', table_name='chunks')
    op.drop_table('chunks')
    op.drop_table('channels')
    op.drop_index(op.f('ix_ingest_jobs_document_id'), table_name='ingest_jobs')
    op.drop_table('ingest_jobs')
    op.drop_table('document_topics')
    op.drop_table('document_pages')
    op.drop_index('ix_elements_doc_order', table_name='document_elements')
    op.drop_index(op.f('ix_document_elements_document_id'), table_name='document_elements')
    op.drop_table('document_elements')
    op.drop_index(op.f('ix_topic_aliases_topic_id'), table_name='topic_aliases')
    op.drop_table('topic_aliases')
    op.drop_index('ix_documents_status_created', table_name='documents')
    op.drop_index(op.f('ix_documents_status'), table_name='documents')
    op.drop_index(op.f('ix_documents_sha256'), table_name='documents')
    op.drop_table('documents')
    op.drop_index(op.f('ix_users_email'), table_name='users')
    op.drop_table('users')
    op.drop_table('topics')
    # ### end Alembic commands ###
