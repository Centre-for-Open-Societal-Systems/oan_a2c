# Merge hygiene

Every merge conflict this repo has produced so far has had the same shape: two branches
appended something to the end of the same file, or one branch reformatted a file the other
was editing. None of them were disagreements about behaviour. This document records the
three files that keep doing it, what was changed so they stop, and the conventions that keep
the fix working.

## What was changed

| Change                                              | Stops                                                                                       |
| :-------------------------------------------------- | :------------------------------------------------------------------------------------------ |
| `.gitattributes`: `oan_a2c/patches.txt merge=union` | Two branches each appending a patch entry. Git now keeps both lines instead of conflicting. |
| `.gitattributes`: `* text=auto eol=lf`              | A CRLF checkout being committed back as "every line changed".                               |
| `.pre-commit-config.yaml`: `end-of-file-fixer`      | A file with no final newline, which makes every append rewrite its last line.               |
| `.pre-commit-config.yaml`: prettier on `*.md`       | Formatting drift in `docs/` — padded vs. compact tables, blank lines added and stripped.    |
| `oan_a2c/tests/test_auth.py` split by topic         | Two branches appending test methods to the end of one 600-line class.                       |

CI runs `pre-commit` on every pull request, so the formatting rules hold whether or not you
installed the hooks locally. Install them anyway — it is the difference between fixing this
before you push and after a red build:

```bash
cd apps/oan_a2c && pre-commit install
```

## `oan_a2c/patches.txt` is append-only

Add your patch on a new line at the end. Do not reorder existing lines, do not tidy the list,
and keep the trailing newline.

`merge=union` makes Git keep both sides' lines when two branches append. That is the correct
answer for a list of patches — order between independent patches does not matter and
`bench migrate` skips any patch already recorded in the Patch Log. It is only correct while
the file is append-only. If you ever need to rename or drop a patch entry, expect union to
keep the stale line as well, and read the merged file before committing.

## Tests: a new class goes in a new file

`test_auth.py`, `test_a2c_lead.py` and `test_loan_api.py` are the files every feature branch
wants to touch. When you add coverage:

- **New area of behaviour → new module**, named `test_<area>_<topic>.py`. Two branches then
  add two files instead of colliding on one.
- Shared request scaffolding lives in `oan_a2c/tests/request_context.py`
  (`RequestContextMixin`). Import it; do not copy it into your module.
- **Do not extend the `test_NN_` numbering** in `TestAuthAPI`. Those prefixes force
  alphabetical execution order for tests that share one class fixture, so every branch that
  adds a test wants the same next number and the same last line of the file. New tests get
  descriptive names in their own class.
- Keep the per-run fixture rules from `docs/refactor-test-isolation.md`: create fixtures with
  a `frappe.generate_hash()` suffix and tear them down in `tearDownClass`, so a class can run
  on its own in any order.

Because auth tests now span more than one module, run the whole area rather than one module:

```bash
bench --site development.localhost run-tests --app oan_a2c --module oan_a2c.tests.test_auth
bench --site development.localhost run-tests --app oan_a2c --module oan_a2c.tests.test_auth_must_change_password
```

## Docs: never reformat what you are not editing

`docs/api-flow-*.md` and `docs/architecture_and_api_spec.md` are edited by nearly every
branch. Two rules keep them mergeable:

1. **Let prettier do the formatting.** Do not hand-pad tables, and do not add or strip blank
   lines around headings to match your own taste. A branch that reformats a file turns every
   nearby edit by another branch into a conflict, which is exactly how a one-sentence
   correction to `7.2 forgot_password` ended up as a three-hunk conflict.
2. **Append endpoint sections at the end of their chapter** and take the next number. When two
   branches both claim the same number — say both add a `7.9` — the resolution is to keep both
   sections and renumber the second one. Never resolve by deleting a section; that silently
   drops an endpoint from the contract, and it is the one merge mistake here that is expensive
   to notice later.

When you reference another endpoint, name it by its path or its method
(`set_initial_password`) as well as its number, so a renumbering does not leave the reference
pointing at the wrong section.

## Two Git settings worth having

Neither is repo state; set them once on your machine.

```bash
# Show the common ancestor in a conflict, not just the two sides. Makes it obvious
# which side actually changed something and which is just the base text.
git config --global merge.conflictStyle zdiff3

# Remember how you resolved a conflict and replay it automatically the next time the
# same one shows up — which it will, on every re-merge of a long-lived branch.
git config --global rerere.enabled true
```

Merge `develop` into your feature branch early and often. All of the conflicts above were
cheap to resolve individually and only became a pile because the branch sat unmerged while
`develop` moved.
