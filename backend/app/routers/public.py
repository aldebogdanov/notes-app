from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from ..deps import get_db
from ..models import Note
from ..schemas import PublicNoteOut

# Deliberately separate router: the only unauthenticated surface besides
# auth and /healthz. Nothing here may depend on get_current_user.
router = APIRouter(prefix="/public", tags=["public"])


@router.get("/notes/{token}", response_model=PublicNoteOut)
def public_note(token: str, response: Response, db: Session = Depends(get_db)) -> Note:
    note = (
        db.query(Note).filter(Note.share_token == token, Note.archived_at.is_(None)).one_or_none()
    )
    if note is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Note not found")
    # Revocation must take effect immediately — never cache the shared view.
    response.headers["Cache-Control"] = "no-store"
    return note
