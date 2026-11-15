"""Unicode-preserving URL slug helper."""

import unicodedata

__all__ = ["slugify_unicode"]


def slugify_unicode(text: str) -> str:
    """Lowercase, keep letters, digits and combining marks of any script, join runs with single hyphens."""
    normal = unicodedata.normalize("NFC", text).lower()
    parts = []
    current = []
    for ch in normal:
        if ch.isalnum() or unicodedata.category(ch).startswith("M"):
            current.append(ch)
        elif current:
            parts.append("".join(current))
            current = []
    if current:
        parts.append("".join(current))
    return "-".join(parts)
