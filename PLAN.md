# Plan — textkit (Code Tower sandbox)

**Goal:** prove the Code Tower loop. The tower finds failing exit checks, works them with parallel workers, and leaves checked draft PRs that Adam can approve on his phone in about 10 minutes.

**Done when (C1, tower by day):** Adam starts only the tower; 2+ workers run in parallel in separate worktrees; CI and the checker pass; 2+ draft PRs; one round entry; 0 merges.

## Phase P1: basics (done 2026-11-14, 4/4)

| ID | Exit check | Depends on |
|---|---|---|
| E1 | `slugify(text)` turns text into a URL slug | none |
| E2 | `word_count(text)` counts words | none |
| E3 | `truncate(text, limit)` shortens text and ends it with "…" | none |
| E4 | README has a "Usage" section showing all four functions | E1, E2, E3 |

## Phase P2: skeleton (current; rows approved by Adam's merge of this change)

| ID | Exit check | Depends on |
|---|---|---|
| E5 | `slugify_unicode(text)` makes a slug that keeps letters and digits of any script | none |
| E6 | `run_command(argv)` runs one textkit command from command-line words and returns the text to show | none |
| E7 | README "Usage" section also shows `slugify_unicode` and `run_command` | E5, E6 |

E6 is the command-line tool's core as a plain function (no printing, no exit): row code may not print or read the terminal, so the thin terminal wrapper stays outside the tower's rows.

## Machine-readable exit checks

Code reads this block. Change it in the same edit as the table above, through a `rules/` PR.

```json exit-checks
{
  "phase": "P2",
  "checks": [
    {"id": "E1", "what": "slugify(text) turns text into a URL slug", "command": "python3 -I checks/e1_slugify.py", "verifier": "machine", "depends_on": []},
    {"id": "E2", "what": "word_count(text) counts words", "command": "python3 -I checks/e2_word_count.py", "verifier": "machine", "depends_on": []},
    {"id": "E3", "what": "truncate(text, limit) shortens text and ends it with an ellipsis", "command": "python3 -I checks/e3_truncate.py", "verifier": "machine", "depends_on": []},
    {"id": "E4", "what": "README has a Usage section showing all four functions", "command": "python3 -I checks/e4_readme_usage.py", "verifier": "machine", "depends_on": ["E1", "E2", "E3"]},
    {"id": "E5", "what": "slugify_unicode(text) makes a slug that keeps letters and digits of any script", "command": "python3 -I checks/e5_slugify_unicode.py", "verifier": "machine", "depends_on": []},
    {"id": "E6", "what": "run_command(argv) runs one textkit command and returns the text to show", "command": "python3 -I checks/e6_run_command.py", "verifier": "machine", "depends_on": []},
    {"id": "E7", "what": "README Usage section also shows slugify_unicode and run_command", "command": "python3 -I checks/e7_readme_p2.py", "verifier": "machine", "depends_on": ["E5", "E6"]}
  ]
}
```
