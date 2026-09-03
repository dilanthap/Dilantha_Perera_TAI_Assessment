"""Routes for the policy library — the retrieval-grounded extension.

Where app/main.py's quiz loop puts one short policy's full text in context on
every call, this router is the other end of that same tradeoff: a library of
several documents, too much combined text to send on every question, so
questions are answered from retrieved excerpts instead. See app/rag.py for the
chunking/embedding/retrieval mechanics, and app/generation.py's module
docstring for why the quiz loop deliberately does NOT work this way.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app import llm
from app.database import get_db
from app.models import LibraryDocument
from app.rag import answer_library_question, ingest_document
from app.templating import templates

router = APIRouter()

MAX_UPLOAD_BYTES = 2_000_000  # ~2MB; library documents can run longer than one policy
TEXT_EXTENSIONS = {".txt", ".md", ".markdown"}


def _error(request: Request, message: str, status_code: int = 400) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="error.html",
        context={"message": message, "demo_mode": llm.DEMO_MODE},
        status_code=status_code,
    )


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


@router.post("/library/documents")
async def upload_document(
    request: Request,
    title: str = Form(""),
    document_text: str = Form(""),
    document_file: UploadFile | None = None,
    db: Session = Depends(get_db),
):
    """Chunk and embed one document into the library."""
    text = (document_text or "").strip()

    if document_file is not None and document_file.filename:
        suffix = Path(document_file.filename).suffix.lower()
        if suffix and suffix not in TEXT_EXTENSIONS:
            return _error(
                request,
                f"Unsupported file type '{suffix}'. Upload a .txt or .md file, "
                "or paste the text directly.",
            )
        raw = await document_file.read()
        if len(raw) > MAX_UPLOAD_BYTES:
            return _error(request, "That file is larger than 2MB.")
        try:
            text = raw.decode("utf-8").strip()
        except UnicodeDecodeError:
            return _error(
                request,
                "That file isn't readable as UTF-8 text. Upload a plain .txt "
                "or .md file, or paste the text directly.",
            )

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

    clean_title = (title or "").strip() or "Untitled document"

    try:
        ingest_document(db, title=clean_title[:255], raw_text=text)
    except llm.LLMError as exc:
        return _error(request, str(exc), status_code=502)

    return RedirectResponse(url="/library", status_code=303)


@router.post("/library/ask", response_class=HTMLResponse)
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
