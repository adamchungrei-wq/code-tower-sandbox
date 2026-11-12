"""URL slug helper."""

import re

__all__ = ["slugify"]


def slugify(text: str) -> str:
    """Lowercase text and join its ASCII letter/digit runs with single hyphens."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
