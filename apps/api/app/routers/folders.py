"""Folders.

A folder is two things at once, which is what the workspace asked for: a
reusable set of documents that any number of sessions can be opened against,
*and* a project view listing the sessions scoped to it.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import selectinload

from api.app.db.models import Document, Folder, FolderDocument, Session as ChatSession
from api.app.deps import CurrentUser, DbSession
from api.app.schemas import FolderCreate, FolderOut, UserOut

router = APIRouter(prefix="/folders", tags=["folders"])


def _user_out(u) -> UserOut | None:
    return UserOut(id=u.id, email=u.email, name=u.name, role=u.role.value) if u else None


@router.post("", response_model=FolderOut, status_code=status.HTTP_201_CREATED)
async def create_folder(body: FolderCreate, db: DbSession, user: CurrentUser) -> FolderOut:
    folder = Folder(name=body.name.strip(), description=body.description,
                    created_by=user.id)
    db.add(folder)
    await db.flush()

    for doc_id in dict.fromkeys(body.document_ids):
        if await db.get(Document, doc_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"문서를 찾을 수 없습니다: {doc_id}")
        db.add(FolderDocument(folder_id=folder.id, document_id=doc_id, added_by=user.id))
    await db.flush()
    await db.refresh(folder, ["creator"])
    return FolderOut(id=folder.id, name=folder.name, description=folder.description,
                     document_count=len(set(body.document_ids)), session_count=0,
                     created_by=_user_out(folder.creator), created_at=folder.created_at)


@router.get("", response_model=list[FolderOut])
async def list_folders(db: DbSession, user: CurrentUser) -> list[FolderOut]:
    doc_counts = dict((await db.execute(
        select(FolderDocument.folder_id, func.count()).group_by(FolderDocument.folder_id)
    )).all())
    session_counts = dict((await db.execute(
        select(ChatSession.folder_id, func.count())
        .where(ChatSession.folder_id.isnot(None)).group_by(ChatSession.folder_id)
    )).all())

    folders = (await db.execute(
        select(Folder).options(selectinload(Folder.creator)).order_by(Folder.created_at.desc())
    )).scalars().all()
    return [
        FolderOut(id=f.id, name=f.name, description=f.description,
                  document_count=doc_counts.get(f.id, 0),
                  session_count=session_counts.get(f.id, 0),
                  created_by=_user_out(f.creator), created_at=f.created_at)
        for f in folders
    ]


@router.get("/{folder_id}", response_model=FolderOut)
async def get_folder(folder_id: UUID, db: DbSession, user: CurrentUser) -> FolderOut:
    folder = await _require(db, folder_id)
    docs = await db.scalar(select(func.count()).select_from(FolderDocument)
                           .where(FolderDocument.folder_id == folder_id))
    sessions = await db.scalar(select(func.count()).select_from(ChatSession)
                               .where(ChatSession.folder_id == folder_id))
    return FolderOut(id=folder.id, name=folder.name, description=folder.description,
                     document_count=docs or 0, session_count=sessions or 0,
                     created_by=_user_out(folder.creator), created_at=folder.created_at)


@router.get("/{folder_id}/documents", response_model=list[UUID])
async def folder_documents(folder_id: UUID, db: DbSession, user: CurrentUser) -> list[UUID]:
    await _require(db, folder_id)
    return list((await db.execute(
        select(FolderDocument.document_id).where(FolderDocument.folder_id == folder_id)
    )).scalars().all())


@router.put("/{folder_id}/documents", response_model=list[UUID])
async def set_folder_documents(folder_id: UUID, document_ids: list[UUID],
                               db: DbSession, user: CurrentUser) -> list[UUID]:
    """Replace the folder's document set.

    Sessions already opened against this folder pick the change up on their next
    turn, which is usually what someone means by "add this to the project".
    """
    await _require(db, folder_id)
    await db.execute(delete(FolderDocument).where(FolderDocument.folder_id == folder_id))
    for doc_id in dict.fromkeys(document_ids):
        if await db.get(Document, doc_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"문서를 찾을 수 없습니다: {doc_id}")
        db.add(FolderDocument(folder_id=folder_id, document_id=doc_id, added_by=user.id))
    await db.flush()
    return list(dict.fromkeys(document_ids))


@router.delete("/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_folder(folder_id: UUID, db: DbSession, user: CurrentUser) -> None:
    """Delete the folder only. Its sessions survive with folder_id cleared, so a
    conversation is never destroyed as a side effect of tidying folders."""
    await _require(db, folder_id)
    await db.execute(delete(Folder).where(Folder.id == folder_id))


async def _require(db, folder_id: UUID) -> Folder:
    folder = (await db.execute(
        select(Folder).options(selectinload(Folder.creator)).where(Folder.id == folder_id)
    )).scalar_one_or_none()
    if folder is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "폴더를 찾을 수 없습니다")
    return folder
