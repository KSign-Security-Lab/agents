"""SQLAlchemy models.

Two design points carry most of the product's weight:

* ``ChunkSpan`` maps *chunk text offsets* to *page + bbox*. Without it a
  citation can only highlight a whole ~512-token blob; with it we can light up
  exactly the sentences that supported a claim.
* ``Message.parent_id`` makes the conversation a tree, so "revert to a
  checkpoint" forks a branch instead of destroying history.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from pgvector.sqlalchemy import SPARSEVEC, Vector
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# ---------------------------------------------------------------- enums ----
class UserRole(str, enum.Enum):
    admin = "admin"
    member = "member"


class SourceKind(str, enum.Enum):
    pdf = "pdf"          # native text PDF
    scanned = "scanned"  # PDF with no text layer -> OCR
    office = "office"    # docx / xlsx / pptx
    hwp = "hwp"          # hwp / hwpx
    image = "image"
    text = "text"        # txt / md / csv
    audio = "audio"
    video = "video"


class DocStatus(str, enum.Enum):
    pending = "pending"
    converting = "converting"
    extracting = "extracting"
    transcribing = "transcribing"
    chunking = "chunking"
    embedding = "embedding"
    categorizing = "categorizing"
    ready = "ready"
    failed = "failed"


class ElementKind(str, enum.Enum):
    heading = "heading"
    paragraph = "paragraph"
    table = "table"
    figure = "figure"
    list_item = "list_item"
    caption = "caption"
    transcript = "transcript"


class MessageRole(str, enum.Enum):
    user = "user"
    assistant = "assistant"
    system = "system"


class MessageStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    complete = "complete"
    failed = "failed"
    cancelled = "cancelled"


# ---------------------------------------------------------------- users ----
class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role"), default=UserRole.member, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Reserved so an OIDC provider can be added later without a migration.
    external_id: Mapped[str | None] = mapped_column(String(255), unique=True)


# ------------------------------------------------------------ documents ----
class Document(Base, TimestampMixin):
    """One uploaded file. The workspace is shared, so there is no owner scope —
    ``uploader_id`` exists for attribution only."""

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = _uuid_pk()
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    mime: Mapped[str] = mapped_column(String(160), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_kind: Mapped[SourceKind] = mapped_column(Enum(SourceKind, name="source_kind"), nullable=False)
    status: Mapped[DocStatus] = mapped_column(
        Enum(DocStatus, name="doc_status"), default=DocStatus.pending, nullable=False, index=True
    )
    error: Mapped[str | None] = mapped_column(Text)

    # storage keys, resolved by services.storage
    key_original: Mapped[str] = mapped_column(String(512), nullable=False)
    key_pdf: Mapped[str | None] = mapped_column(String(512))   # None for audio/video
    key_media: Mapped[str | None] = mapped_column(String(512)) # normalized a/v for the player

    page_count: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)

    # produced by the categorization stage
    summary: Mapped[str | None] = mapped_column(Text)
    key_entities: Mapped[list | None] = mapped_column(JSONB)
    suggested_questions: Mapped[list | None] = mapped_column(JSONB)

    uploader_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    uploader: Mapped[User | None] = relationship(lazy="joined")
    pages: Mapped[list["DocumentPage"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("sha256", name="uq_documents_sha256"),
        Index("ix_documents_status_created", "status", "created_at"),
    )


class DocumentPage(Base):
    """Page geometry. ``width``/``height``/``rotation`` are what the browser
    needs to scale stored bboxes onto a rendered PDF.js viewport."""

    __tablename__ = "document_pages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)  # 1-based
    width: Mapped[float] = mapped_column(Float, nullable=False)    # PDF points
    height: Mapped[float] = mapped_column(Float, nullable=False)
    rotation: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    has_text_layer: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    key_thumb: Mapped[str | None] = mapped_column(String(512))

    document: Mapped[Document] = relationship(back_populates="pages")

    __table_args__ = (UniqueConstraint("document_id", "page_no", name="uq_page_per_doc"),)


class DocumentElement(Base):
    """Layout element from Docling: reading order, heading level, and — for
    tables — full cell structure with per-cell bboxes so a numeric answer can
    cite the exact cell it read."""

    __tablename__ = "document_elements"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    page_no: Mapped[int | None] = mapped_column(Integer)
    kind: Mapped[ElementKind] = mapped_column(Enum(ElementKind, name="element_kind"), nullable=False)
    level: Mapped[int | None] = mapped_column(Integer)          # heading depth
    reading_order: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str | None] = mapped_column(Text)
    bbox: Mapped[list | None] = mapped_column(JSONB)            # [x0,y0,x1,y1] page space
    table_json: Mapped[dict | None] = mapped_column(JSONB)      # {n_rows,n_cols,cells:[...]}

    __table_args__ = (Index("ix_elements_doc_order", "document_id", "reading_order"),)


class Chunk(Base):
    """Retrieval unit. Carries both a dense vector and bge-m3's sparse lexical
    weights, so hybrid search needs no separate search engine and no Korean
    text-search tokenizer."""

    __tablename__ = "chunks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ord: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    heading_path: Mapped[str | None] = mapped_column(Text)      # "3. 계약조건 > 3.2 지급조건"
    token_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # paged documents
    page_from: Mapped[int | None] = mapped_column(Integer)
    page_to: Mapped[int | None] = mapped_column(Integer)
    # audio / video
    t_start_ms: Mapped[int | None] = mapped_column(Integer)
    t_end_ms: Mapped[int | None] = mapped_column(Integer)

    is_table: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    element_id: Mapped[int | None] = mapped_column(
        ForeignKey("document_elements.id", ondelete="SET NULL")
    )

    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024))
    sparse: Mapped[dict | None] = mapped_column(SPARSEVEC(250002))

    document: Mapped[Document] = relationship(back_populates="chunks")
    spans: Mapped[list["ChunkSpan"]] = relationship(
        back_populates="chunk", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("document_id", "ord", name="uq_chunk_per_doc"),
        Index("ix_chunks_doc_page", "document_id", "page_from"),
    )


class ChunkSpan(Base):
    """Line-granular map from chunk text offsets to page geometry.

    This is the table that turns "somewhere on page 14" into a highlight over
    the exact sentences that were cited.
    """

    __tablename__ = "chunk_spans"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    chunk_id: Mapped[int] = mapped_column(
        ForeignKey("chunks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    text_start: Mapped[int] = mapped_column(Integer, nullable=False)  # offset into Chunk.text
    text_end: Mapped[int] = mapped_column(Integer, nullable=False)
    page_no: Mapped[int | None] = mapped_column(Integer)
    bbox: Mapped[list | None] = mapped_column(JSONB)
    t_start_ms: Mapped[int | None] = mapped_column(Integer)
    t_end_ms: Mapped[int | None] = mapped_column(Integer)

    chunk: Mapped[Chunk] = relationship(back_populates="spans")

    __table_args__ = (
        CheckConstraint("text_end >= text_start", name="ck_span_range"),
        Index("ix_spans_chunk_range", "chunk_id", "text_start"),
    )


# ---------------------------------------------------------------- topics ---
class Topic(Base, TimestampMixin):
    """Emergent taxonomy: the agent proposes names, and near-duplicates are
    merged by embedding similarity rather than by a fixed list."""

    __tablename__ = "topics"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(180), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024))
    doc_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_by: Mapped[str] = mapped_column(String(16), default="agent", nullable=False)
    merged_into_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("topics.id", ondelete="SET NULL")
    )


class TopicAlias(Base):
    """Surface forms the agent produced that were folded into a topic — kept so
    the merge history is auditable and reversible."""

    __tablename__ = "topic_aliases"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    topic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), nullable=False, index=True
    )
    alias: Mapped[str] = mapped_column(String(160), nullable=False)

    __table_args__ = (UniqueConstraint("topic_id", "alias", name="uq_topic_alias"),)


class DocumentTopic(Base):
    __tablename__ = "document_topics"

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    topic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), primary_key=True
    )
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    source: Mapped[str] = mapped_column(String(16), default="agent", nullable=False)


# ------------------------------------------------------------- channels ----
class Channel(Base, TimestampMixin):
    """A named, shared space: one continuous message feed plus the document
    set the agent answers from. Merges what used to be Folder (document set +
    project view) and Session (the conversation) into one entity — a channel
    is no longer layered, it just is the conversation."""

    __tablename__ = "channels"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    # Marks the live path through the message tree. Reverting a checkpoint just
    # moves this pointer; nothing is deleted.
    active_leaf_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL", use_alter=True)
    )
    archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    creator: Mapped[User | None] = relationship(lazy="joined", foreign_keys=[created_by])

    __table_args__ = (UniqueConstraint("name", name="uq_channels_name"),)


class ChannelDocument(Base):
    """A channel's document set — flat and absolute, no add/remove delta
    layer, since there's nothing above a channel to layer on top of."""

    __tablename__ = "channel_documents"

    channel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), primary_key=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    added_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


# -------------------------------------------------------------- messages ---
class Message(Base):
    """A node in the conversation tree.

    ``parent_id`` makes branching natural: editing or reverting creates a
    sibling rather than mutating history, so every earlier path stays readable.
    ``author_id`` is what makes the shared channel legible — you can always
    see who asked what.
    """

    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = _uuid_pk()
    channel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), nullable=False, index=True
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), index=True
    )
    # Stable id labeling which branch this message belongs to. Not currently
    # read by the agent turn driver (hand-rolled asyncio, not LangGraph) — it
    # would become a checkpointer thread key if AsyncPostgresSaver is ever
    # wired in (see docs/STATUS.md's "Not yet done").
    branch_root_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)

    role: Mapped[MessageRole] = mapped_column(Enum(MessageRole, name="message_role"), nullable=False)
    author_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[MessageStatus] = mapped_column(
        Enum(MessageStatus, name="message_status"), default=MessageStatus.complete, nullable=False
    )
    model: Mapped[str | None] = mapped_column(String(200))
    token_usage: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    author: Mapped[User | None] = relationship(lazy="joined")
    citations: Mapped[list["Citation"]] = relationship(
        back_populates="message", cascade="all, delete-orphan", order_by="Citation.idx"
    )

    __table_args__ = (Index("ix_messages_channel_created", "channel_id", "created_at"),)


class Citation(Base):
    """A resolved reference behind one inline pill.

    Everything the UI needs to render a hover tooltip and to open the source at
    the right place is denormalized here, so rendering an old message never
    depends on re-running retrieval.
    """

    __tablename__ = "citations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    idx: Mapped[int] = mapped_column(Integer, nullable=False)  # the number in the pill
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    chunk_id: Mapped[int | None] = mapped_column(ForeignKey("chunks.id", ondelete="SET NULL"))

    page_no: Mapped[int | None] = mapped_column(Integer)
    # Highlight rectangles, already tightened to the supporting sentences:
    # [{"page_no": 14, "bbox": [x0,y0,x1,y1]}, ...]
    rects: Mapped[list | None] = mapped_column(JSONB)
    t_start_ms: Mapped[int | None] = mapped_column(Integer)
    t_end_ms: Mapped[int | None] = mapped_column(Integer)

    snippet: Mapped[str] = mapped_column(Text, default="", nullable=False)
    heading_path: Mapped[str | None] = mapped_column(Text)
    score: Mapped[float | None] = mapped_column(Float)
    # True when the agent had to look outside the channel's selected documents.
    out_of_scope: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    message: Mapped[Message] = relationship(back_populates="citations")

    __table_args__ = (UniqueConstraint("message_id", "idx", name="uq_citation_idx"),)


# ------------------------------------------------------ agent observability -
class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    graph: Mapped[str] = mapped_column(String(80), default="main", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="running", nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    # Citations the model invented that did not match a retrieved source.
    rejected_citations: Mapped[list | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AgentStep(Base):
    __tablename__ = "agent_steps"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ord: Mapped[int] = mapped_column(Integer, nullable=False)
    node: Mapped[str] = mapped_column(String(80), nullable=False)
    label: Mapped[str | None] = mapped_column(String(300))
    input_json: Mapped[dict | None] = mapped_column(JSONB)
    output_json: Mapped[dict | None] = mapped_column(JSONB)
    latency_ms: Mapped[int | None] = mapped_column(Integer)


class IngestJob(Base):
    """Per-stage ingest state, so a failure resumes instead of restarting a
    30-minute OCR run from scratch."""

    __tablename__ = "ingest_jobs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stage: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (UniqueConstraint("document_id", "stage", name="uq_job_stage"),)
