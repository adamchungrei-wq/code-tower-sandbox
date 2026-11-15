"""Core of a small command-line tool: run one textkit command."""

import textkit

__all__ = ["run_command"]


def run_command(argv: list[str]) -> str:
    """Run a command given as words (name, then arguments) and return the text to show.

    Commands: slugify TEXT, word-count TEXT, truncate TEXT LIMIT, normalize TEXT.
    Raises ValueError with a message for an empty line, unknown command or bad arguments.
    """
    if not argv:
        raise ValueError("no command given")
    name, args = argv[0], argv[1:]
    if name == "truncate":
        if len(args) != 2:
            raise ValueError("truncate takes 2 arguments: TEXT LIMIT")
        try:
            limit = int(args[1])
        except ValueError:
            raise ValueError(f"truncate LIMIT must be a whole number, got {args[1]!r}") from None
        return textkit.truncate(args[0], limit)
    single = {
        "slugify": textkit.slugify,
        "word-count": textkit.word_count,
        "normalize": textkit.normalize_whitespace,
    }
    if name not in single:
        raise ValueError(f"unknown command: {name}")
    if len(args) != 1:
        raise ValueError(f"{name} takes 1 argument: TEXT")
    return str(single[name](args[0]))
