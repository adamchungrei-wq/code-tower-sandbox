"""Whitespace helpers."""

__all__ = ["normalize_whitespace"]


def normalize_whitespace(text: str) -> str:
    """Collapse every run of whitespace to one space and strip both ends."""
    return " ".join(text.split())
