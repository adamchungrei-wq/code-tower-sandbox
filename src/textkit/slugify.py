"""URL slug helper."""

import re
import unicodedata

__all__ = ["slugify"]


def slugify(text: str) -> str:
    """Fold accented letters, lowercase, and join ASCII letter/digit runs with single hyphens."""
    decomposed = unicodedata.normalize("NFKD", text)
    folded = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")
