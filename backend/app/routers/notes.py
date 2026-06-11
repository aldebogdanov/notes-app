import io
import re
import secrets
import zipfile
from calendar import monthrange
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import case, or_
from sqlalchemy.orm import Session, selectinload

from ..deps import get_current_user, get_db
from ..models import Note, User
from ..schemas import (
    BulkDeleteIn,
    CalendarDay,
    NoteIn,
    NoteOut,
    NotesPage,
)

router = APIRouter(prefix="/notes", tags=["notes"])


def _normalize_tags(tags: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in tags:
        t = raw.strip().lower()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _own_note_or_404(note_id: int, user: User, db: Session) -> Note:
    note = db.get(Note, note_id)
    if note is None or note.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Note not found")
    return note


def _now() -> datetime:
    return datetime.now(UTC)


def _notify_scheduler(request: Request) -> None:
    """Note dates changed — let the reminder loop recompute its wake-up."""
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is not None:
        scheduler.notify_change()


def _slug(title: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return cleaned[:40] or "note"


def _note_markdown(note: Note) -> str:
    return f"# {note.title}\n\n{note.content}"


def _zip_response(notes: list[Note], filename: str) -> Response:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for n in notes:
            archive.writestr(f"{n.id}-{_slug(n.title)}.md", _note_markdown(n))
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("", response_model=NotesPage)
def list_notes(
    q: str | None = None,
    tag: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    archived: bool = False,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> NotesPage:
    # selectinload keeps NoteOut.notification_status derivation out of N+1.
    query = db.query(Note).options(selectinload(Note.notifications)).filter(Note.user_id == user.id)
    if archived:
        query = query.filter(Note.archived_at.is_not(None))
    else:
        query = query.filter(Note.archived_at.is_(None))
    if q:
        pattern = f"%{q}%"
        query = query.filter(or_(Note.title.ilike(pattern), Note.content.ilike(pattern)))
    if date_from:
        query = query.filter(Note.note_date >= date_from)
    if date_to:
        query = query.filter(Note.note_date <= date_to)

    # Tag filter requires JSON-aware logic; apply in Python to stay dialect-agnostic.
    if tag:
        needle = tag.strip().lower()
        candidates = [n for n in query.all() if needle in (n.tags or [])]
        total = len(candidates)
        candidates.sort(
            key=lambda n: (
                0 if n.pinned_at else 1,
                -(n.pinned_at.timestamp() if n.pinned_at else 0.0),
                -n.updated_at.timestamp(),
                -n.id,
            )
        )
        items = candidates[offset : offset + limit]
        return NotesPage(items=items, total=total, limit=limit, offset=offset)

    total = query.count()
    pin_order = case((Note.pinned_at.is_not(None), 0), else_=1)
    items = (
        query.order_by(
            pin_order,
            Note.pinned_at.desc(),
            Note.updated_at.desc(),
            Note.id.desc(),
        )
        .offset(offset)
        .limit(limit)
        .all()
    )
    return NotesPage(items=items, total=total, limit=limit, offset=offset)


@router.post("", response_model=NoteOut, status_code=status.HTTP_201_CREATED)
def create_note(
    request: Request,
    payload: NoteIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Note:
    note = Note(
        user_id=user.id,
        title=payload.title,
        content=payload.content,
        tags=_normalize_tags(payload.tags),
        note_date=payload.note_date,
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    _notify_scheduler(request)
    return note


@router.get("/calendar", response_model=list[CalendarDay])
def calendar(
    year: int = Query(..., ge=1970, le=3000),
    month: int = Query(..., ge=1, le=12),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[CalendarDay]:
    start = date(year, month, 1)
    end = date(year, month, monthrange(year, month)[1])
    notes = (
        db.query(Note)
        .filter(
            Note.user_id == user.id,
            Note.archived_at.is_(None),
            Note.note_date >= start,
            Note.note_date <= end,
        )
        .order_by(Note.note_date.asc(), Note.id.asc())
        .all()
    )
    buckets: dict[date, list[int]] = {}
    for n in notes:
        if n.note_date is not None:
            buckets.setdefault(n.note_date, []).append(n.id)
    return [CalendarDay(date=d, note_ids=ids) for d, ids in sorted(buckets.items())]


# NB: static /export paths must be declared before /{note_id} routes —
# FastAPI matches in declaration order (same reason /calendar sits above).
@router.get("/export")
def export_all(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    notes = db.query(Note).filter(Note.user_id == user.id).order_by(Note.id).all()
    return _zip_response(notes, f"notes-export-{date.today().isoformat()}.zip")


@router.post("/bulk-export")
def bulk_export(
    payload: BulkDeleteIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    # Foreign ids are silently skipped — same idiom as bulk-delete.
    notes = (
        db.query(Note)
        .filter(Note.user_id == user.id, Note.id.in_(payload.ids))
        .order_by(Note.id)
        .all()
    )
    if not notes:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No exportable notes")
    return _zip_response(notes, f"notes-export-{date.today().isoformat()}.zip")


@router.post("/bulk-delete", status_code=status.HTTP_204_NO_CONTENT)
def bulk_delete(
    request: Request,
    payload: BulkDeleteIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    notes = db.query(Note).filter(Note.user_id == user.id, Note.id.in_(payload.ids)).all()
    for n in notes:
        db.delete(n)
    db.commit()
    _notify_scheduler(request)


@router.get("/{note_id}", response_model=NoteOut)
def get_note(
    note_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Note:
    return _own_note_or_404(note_id, user, db)


@router.put("/{note_id}", response_model=NoteOut)
def update_note(
    request: Request,
    note_id: int,
    payload: NoteIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Note:
    note = _own_note_or_404(note_id, user, db)
    note.title = payload.title
    note.content = payload.content
    note.tags = _normalize_tags(payload.tags)
    note.note_date = payload.note_date
    db.commit()
    db.refresh(note)
    _notify_scheduler(request)
    return note


@router.delete("/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_note(
    request: Request,
    note_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    note = _own_note_or_404(note_id, user, db)
    db.delete(note)
    db.commit()
    _notify_scheduler(request)


@router.post("/{note_id}/archive", response_model=NoteOut)
def archive_note(
    request: Request,
    note_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Note:
    note = _own_note_or_404(note_id, user, db)
    if note.archived_at is None:
        note.archived_at = _now()
        db.commit()
        db.refresh(note)
        _notify_scheduler(request)
    return note


@router.post("/{note_id}/unarchive", response_model=NoteOut)
def unarchive_note(
    request: Request,
    note_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Note:
    note = _own_note_or_404(note_id, user, db)
    if note.archived_at is not None:
        note.archived_at = None
        db.commit()
        db.refresh(note)
        _notify_scheduler(request)
    return note


@router.get("/{note_id}/export")
def export_note(
    note_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    note = _own_note_or_404(note_id, user, db)
    return Response(
        content=_note_markdown(note),
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{_slug(note.title)}.md"'},
    )


@router.post("/{note_id}/share", response_model=NoteOut)
def share_note(
    note_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Note:
    note = _own_note_or_404(note_id, user, db)
    if note.share_token is None:
        note.share_token = secrets.token_urlsafe(16)
        db.commit()
        db.refresh(note)
    return note


@router.delete("/{note_id}/share", response_model=NoteOut)
def revoke_share(
    note_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Note:
    note = _own_note_or_404(note_id, user, db)
    if note.share_token is not None:
        note.share_token = None
        db.commit()
        db.refresh(note)
    return note


@router.post("/{note_id}/pin", response_model=NoteOut)
def pin_note(
    note_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Note:
    note = _own_note_or_404(note_id, user, db)
    if note.pinned_at is None:
        note.pinned_at = _now()
        db.commit()
        db.refresh(note)
    return note


@router.post("/{note_id}/unpin", response_model=NoteOut)
def unpin_note(
    note_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Note:
    note = _own_note_or_404(note_id, user, db)
    if note.pinned_at is not None:
        note.pinned_at = None
        db.commit()
        db.refresh(note)
    return note
