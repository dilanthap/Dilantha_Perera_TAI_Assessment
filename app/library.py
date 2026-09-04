"""Routes for the policy library — the retrieval-grounded extension.

Where app/main.py's quiz loop puts one short policy's full text in context on
every call, this router is the other end of that same tradeoff: a library of
several documents, too much combined text to send on every question, so
questions are answered from retrieved excerpts instead. See app/rag.py for the
chunking/embedding/retrieval mechanics, and app/generation.py's module
docstring for why the quiz loop deliberately does NOT work this way.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app import llm
from app.database import get_db
from app.models import LibraryDocument
from app.ratelimit import enforce
from app.rag import answer_library_question, ingest_document
from app.templating import render_error, templates
from app.text_utils import extract_upload_text, title_from_filename

router = APIRouter()

MAX_UPLOAD_BYTES = 2_000_000  # ~2MB; library documents can run longer than one policy


def _error(request: Request, message: str, status_code: int = 400) -> HTMLResponse:
    return render_error(request, message, status_code)


def _all_documents(db: Session) -> list[LibraryDocument]:
    return db.query(LibraryDocument).order_by(LibraryDocument.uploaded_at.desc()).all()


@router.get("/library", response_class=HTMLResponse)
def library_home(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request=request,
        name="library.html",
        context={
            "documents": _all_documents(db),
            "answer": None,
            "question": "",
            "demo_mode": llm.DEMO_MODE,
        },
    )


@router.post("/library/documents", dependencies=[Depends(enforce)])
async def upload_document(
    request: Request,
    title: str = Form(""),
    document_text: str = Form(""),
    document_file: UploadFile | None = None,
    db: Session = Depends(get_db),
):
    """Chunk and embed one document into the library."""
    text = (document_text or "").strip()
    fallback_title = "Untitled document"

    if document_file is not None and document_file.filename:
        raw = await document_file.read()
        if len(raw) > MAX_UPLOAD_BYTES:
            return _error(request, "That file is larger than 2MB.")
        try:
            text = extract_upload_text(document_file, raw)
        except ValueError as exc:
            return _error(request, str(exc))
        fallback_title = title_from_filename(document_file.filename, fallback_title)

    if not text:
        return _error(
            request,
            "No document text was provided. Paste text into the box or upload "
            "a file.",
        )
    if len(text) < 200:
        return _error(
            request,
            "That document looks too short to be useful in the library.",
        )

    clean_title = (title or "").strip() or fallback_title

    try:
        ingest_document(db, title=clean_title[:255], raw_text=text)
    except llm.LLMError as exc:
        return _error(request, str(exc), status_code=502)

    return RedirectResponse(url="/library", status_code=303)


@router.post("/library/documents/{document_id}/delete")
def delete_document(request: Request, document_id: int, db: Session = Depends(get_db)):
    """Remove one document and its chunks from the library.

    Cascade is configured on the ORM relationship (models.py:
    LibraryDocument.chunks, cascade="all, delete-orphan") and the FK
    (ondelete="CASCADE"), so deleting the document also deletes every chunk
    that was embedded from it — nothing else references a LibraryDocument, so
    this is safe to do outright rather than needing a confirmation step
    server-side (the UI's delete button confirms client-side instead).
    """
    document = db.get(LibraryDocument, document_id)
    if document is None:
        return _error(request, "That document could not be found.", status_code=404)

    db.delete(document)
    db.commit()

    return RedirectResponse(url="/library", status_code=303)


@router.post("/library/ask", response_class=HTMLResponse, dependencies=[Depends(enforce)])
def ask_library(request: Request, question: str = Form(""), db: Session = Depends(get_db)):
    """Retrieve across every document in the library and answer from that."""
    q = (question or "").strip()
    if not q:
        return _error(request, "Please enter a question.")

    try:
        result = answer_library_question(db, q)
    except llm.LLMError as exc:
        return _error(request, str(exc), status_code=502)

    return templates.TemplateResponse(
        request=request,
        name="library.html",
        context={
            "documents": _all_documents(db),
            "answer": result,
            "question": q,
            "demo_mode": llm.DEMO_MODE,
        },
    )
