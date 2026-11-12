"""Word counting."""

__all__ = ["word_count"]


def word_count(text: str) -> int:
    """Count words: runs of non-whitespace characters."""
    return len(text.split())
