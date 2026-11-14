# code-tower-sandbox

A practice repo for the Code Tower: a Claude Code session that finds its own work, runs workers in parallel, checks their results and leaves draft PRs for Adam to approve.

The "product" is `textkit`, a tiny Python text-utilities package. `PLAN.md` lists the exit checks the tower works toward.

## Run

```bash
python3 -I -m pytest -q             # tests
python3 -I checks/run_checks.py     # which exit checks pass
```

## Usage

```python
from textkit import normalize_whitespace, slugify, word_count, truncate

normalize_whitespace("  hello   big\n world ")  # "hello big world"
slugify("Hello, World!")                        # "hello-world"
word_count("one two  three")                    # 3
truncate("hello world", 6)                      # "hello…"
```

## Start the tower

Open a Claude Code session on this repo and say "run the tower". It runs one round (see `.claude/skills/code-tower/SKILL.md`) and reports a board, any asks, and its draft PRs.

## Stop it

Set `"mode": "off"` in `rules/project.json`, or close its PRs and delete its `tower/` branches.
