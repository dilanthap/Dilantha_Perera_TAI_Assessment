"""Small, dependency-free text helpers shared by app/main.py and app/library.py.

Lives in its own module rather than either router: main.py already imports
app.library (to mount its router), so library.py importing back from main.py
for a helper like this would be a circular import. Neither router needs to
import the other to reach this.
"""

from __future__ import annotations

from pathlib import Path


def title_from_filename(filename: str, fallback: str) -> str:
    """Turn "acme_ai_policy.md" into "acme ai policy" for use as a default
    title when a file is uploaded without a title being typed in — the
    filename is real, user-supplied information; falling back straight to a
    generic "Untitled..." string throws it away for no reason.
    """
    stem = Path(filename).stem.replace("_", " ").replace("-", " ").strip()
    return stem or fallback
