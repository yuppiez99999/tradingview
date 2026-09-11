---
description: Run the 28-repo four-gate convergence check (ruff incremental + pytest + industrial grade + data validity). This is the ONLY definition of "done" for /speckit.implement and /speckit.converge in this repository.
---

## Gate

Run `scripts/speckit_converge_gate.py` and report PASS/FAIL. Do not soften the result: exit 0 = PASS, anything else = FAIL.

## Steps

1. **Collect changed .py files**: use `git status --short` / `git diff --name-only` plus any files touched in the current session for this feature. Relative to repo root. Markdown-only specs may legitimately have an empty list.

2. **Determine pytest scope**: read the feature's `specs/<feature>/tasks.md` — use the declared test scope (test paths / `-k` expression). If tasks.md declares no test scope, that is itself a FAIL condition: report it and stop.

3. **Run the gate** (from repo root):

   ```powershell
   .venv/Scripts/python.exe scripts/speckit_converge_gate.py --files <changed.py ...> --pytest-args "<scope>"
   ```

4. **Report**:
   - `ALL PASS` → report the four per-gate PASS lines verbatim.
   - `FAIL` → report the failing gate name, its exit code, and the tail output block. State clearly: convergence is NOT established; the remaining work must go back into tasks.md.

## Rules

- Never skip a gate "because it's data-related" or "unrelated to this change" — fail-closed is the design.
- Never claim completion without this gate exiting 0 in this session's transcript.
- On failure, the tail output must be excerpted into `specs/<feature>/implementation-notes.md` before any further implement loop.
