"""Truncate text with an ellipsis."""

__all__ = ["truncate"]


def truncate(text: str, limit: int) -> str:
    """Shorten text to at most `limit` characters, ending with an ellipsis if cut."""
    if limit < 1:
        raise ValueError("limit must be at least 1")
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
