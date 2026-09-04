"""Small, dependency-free text helpers shared by app/main.py and app/library.py.

Lives in its own module rather than either router: main.py already imports
app.library (to mount its router), so library.py importing back from main.py
for a helper like this would be a circular import. Neither router needs to
import the other to reach this.
"""

from __future__ import annotations

import io
from pathlib import Path

from fastapi import UploadFile

# .txt/.md is the reliable path for both routers; .pdf is handled separately
# in extract_upload_text below, best-effort via pypdf.
TEXT_EXTENSIONS = {".txt", ".md", ".markdown"}


def title_from_filename(filename: str, fallback: str) -> str:
    """Turn "acme_ai_policy.md" into "acme ai policy" for use as a default
    title when a file is uploaded without a title being typed in — the
    filename is real, user-supplied information; falling back straight to a
    generic "Untitled..." string throws it away for no reason.
    """
    stem = Path(filename).stem.replace("_", " ").replace("-", " ").strip()
    return stem or fallback


def extract_upload_text(upload: UploadFile, raw: bytes) -> str:
    """Get text out of an uploaded file.

    .txt / .md is the primary path and always works. PDF is a convenience: it
    is isolated here and any pypdf failure becomes a clear message, so a demo
    never depends on PDF parsing quirks. Raises ValueError with a user-safe
    message on anything unreadable — callers render that straight into the
    error page rather than needing their own translation layer.
    """
    suffix = Path(upload.filename or "").suffix.lower()

    if suffix == ".pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(raw))
            pages = [page.extract_text() or "" for page in reader.pages]
        except Exception:
            raise ValueError(
                "That PDF could not be read. PDF support is best-effort — please "
                "paste the text directly, or upload a .txt or .md file."
            ) from None

        text = "\n\n".join(pages).strip()
        if not text:
            raise ValueError(
                "No text could be extracted from that PDF — it may be a scan or "
                "image-only. Please paste the text directly instead."
            )
        return text

    if suffix and suffix not in TEXT_EXTENSIONS:
        raise ValueError(
            f"Unsupported file type '{suffix}'. Upload a .txt, .md or .pdf file, "
            "or paste the text directly."
        )

    try:
        return raw.decode("utf-8").strip()
    except UnicodeDecodeError:
        raise ValueError(
            "That file isn't readable as UTF-8 text. Please upload a plain .txt "
            "or .md file, or paste the text directly."
        ) from None
