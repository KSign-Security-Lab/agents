"""API response shapes.

Deliberately explicit rather than serializing ORM objects: the browser is the
only consumer, and a citation's contract with the viewer (rects, page, time
range) is the most important thing in this file to keep stable.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


# ------------------------------------------------------------------- auth ----
class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    id: UUID
    email: str
    name: str
    role: str


class LoginResponse(BaseModel):
    user: UserOut
    token: str
    expires_in: int


# -------------------------------------------------------------- documents ----
class TopicOut(BaseModel):
    id: UUID
    name: str
    slug: str
    doc_count: int = 0


class PageOut(BaseModel):
    page_no: int
    width: float
    height: float
    rotation: int = 0
    has_text_layer: bool = True


class DocumentOut(BaseModel):
    id: UUID
    filename: str
    mime: str
    size_bytes: int
    source_kind: str
    status: str
    error: str | None = None
    page_count: int | None = None
    duration_ms: int | None = None
    summary: str | None = None
    key_entities: list[str] | None = None
    suggested_questions: list[str] | None = None
    topics: list[TopicOut] = []
    uploader: UserOut | None = None
    created_at: datetime
    has_pdf: bool = False
    has_media: bool = False


class DocumentDetail(DocumentOut):
    pages: list[PageOut] = []
    outline: list[dict[str, Any]] = []


class OutlineItem(BaseModel):
    page_no: int | None
    level: int | None
    text: str
    bbox: list[float] | None = None


# --------------------------------------------------------------- channels ----
class ChannelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    # Already-ingested documents to attach at creation time.
    document_ids: list[UUID] = []


class ChannelUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None


class ChannelOut(BaseModel):
    id: UUID
    name: str
    description: str | None = None
    created_by: UserOut | None = None
    active_leaf_id: UUID | None = None
    message_count: int = 0
    document_count: int = 0
    archived: bool = False
    created_at: datetime
    updated_at: datetime


class ScopeUpdate(BaseModel):
    add: list[UUID] = []
    remove: list[UUID] = []


# --------------------------------------------------------------- messages ----
class CitationOut(BaseModel):
    """What one inline pill needs, both for the hover card and the viewer."""

    idx: int
    document_id: UUID
    filename: str
    chunk_id: int | None = None
    page_no: int | None = None
    # [{"page_no": 14, "bbox": [x0,y0,x1,y1]}] in PDF points, top-left origin.
    rects: list[dict[str, Any]] = []
    t_start_ms: int | None = None
    t_end_ms: int | None = None
    snippet: str = ""
    heading_path: str | None = None
    out_of_scope: bool = False


class MessageOut(BaseModel):
    id: UUID
    channel_id: UUID
    parent_id: UUID | None = None
    role: str
    author: UserOut | None = None
    content: str
    status: str
    citations: list[CitationOut] = []
    created_at: datetime
    # Sibling navigation for the branch switcher ("< 2/3 >").
    sibling_index: int = 0
    sibling_count: int = 1


class SendMessage(BaseModel):
    content: str = Field(min_length=1)
    # Reply under a specific message to fork deliberately; defaults to the
    # channel's active leaf.
    parent_id: UUID | None = None


class RevertRequest(BaseModel):
    """Move the live path back to a checkpoint. Nothing is deleted; the next
    message forks a new branch from here."""

    message_id: UUID


class BranchOut(BaseModel):
    message_id: UUID
    preview: str
    created_at: datetime
    is_active: bool


# ------------------------------------------------------------------ topics ---
class TopicRename(BaseModel):
    name: str = Field(min_length=1, max_length=160)


class TopicMerge(BaseModel):
    keep_id: UUID
    drop_id: UUID


class MergeCandidate(BaseModel):
    similarity: float
    suggested_keep: dict[str, Any]
    suggested_drop: dict[str, Any]


# ------------------------------------------------------------------ search ---
class SearchHit(BaseModel):
    chunk_id: int
    document_id: UUID
    filename: str
    text: str
    heading_path: str | None = None
    page_from: int | None = None
    t_start_ms: int | None = None
    score: float
    rerank_score: float | None = None


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    document_ids: list[UUID] | None = None
    topic_ids: list[UUID] | None = None
    top_k: int = 20
    rerank: bool = True


# ------------------------------------------------------------------ events ---
EventType = Literal["token", "citation", "step", "message", "presence",
                    "document.status", "document.progress", "document.categorized",
                    "done", "error"]
