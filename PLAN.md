# Plan — textkit (Code Tower sandbox)

**Goal:** prove the Code Tower loop. The tower finds failing exit checks, works them with parallel workers, and leaves checked draft PRs that Adam can approve on his phone in about 10 minutes.

**Done when (C1, tower by day):** Adam starts only the tower; 2+ workers run in parallel in separate worktrees; CI and the checker pass; 2+ draft PRs; one round entry; 0 merges.

## Phase P1: basics (current)

| ID | Exit check | Depends on |
|---|---|---|
| E1 | `slugify(text)` turns text into a URL slug | none |
| E2 | `word_count(text)` counts words | none |
| E3 | `truncate(text, limit)` shortens text and ends it with "…" | none |
| E4 | README has a "Usage" section showing all four functions | E1, E2, E3 |

## Phase P2: skeleton (not started)

Unicode-aware slugs; a small command-line tool. Its rows get written when P1 passes and Adam approves the next phase.

## Machine-readable exit checks

Code reads this block. Change it in the same edit as the table above, through a `rules/` PR.

```json exit-checks
{
  "phase": "P1",
  "checks": [
    {"id": "E1", "what": "slugify(text) turns text into a URL slug", "command": "python3 -I checks/e1_slugify.py", "verifier": "machine", "depends_on": []},
    {"id": "E2", "what": "word_count(text) counts words", "command": "python3 -I checks/e2_word_count.py", "verifier": "machine", "depends_on": []},
    {"id": "E3", "what": "truncate(text, limit) shortens text and ends it with an ellipsis", "command": "python3 -I checks/e3_truncate.py", "verifier": "machine", "depends_on": []},
    {"id": "E4", "what": "README has a Usage section showing all four functions", "command": "python3 -I checks/e4_readme_usage.py", "verifier": "machine", "depends_on": ["E1", "E2", "E3"]}
  ]
}
```
