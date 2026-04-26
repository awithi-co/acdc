---
name: resume-codex-session
description: Use when the user asks Claude to continue, resume, or take over work from a Codex session by session name or session id (UUID or UUID prefix). Examples include "continue the codex session called feature-x" or "/resume-codex-session 019dc08e". Do not use for ordinary Claude resume commands or when the user only asks about Claude's own session history.
---

# Resume Codex session

## Purpose

When Claude takes over work from a Codex session, reconstruct the current state by reading both the Codex storage and the actual working directory. Do not hand off based on only the last few conversation lines.

## Procedure

1. Determine the query.
   - Input is a session **name** (e.g. `feature-x`) or a **session id** (UUID or UUID prefix, e.g. `019dc08e`). Optionally a working-directory or project hint from the user.
   - If the query looks like a UUID (8+ hex, dashes allowed), go straight to the id lookup path — it matches on the filesystem and works even when the session is not in the index.
   - Otherwise, use the name path. `codex feature-x` means `feature-x`.
   - If several names are plausible, confirm briefly once before a wider search.

2. Find session candidates.

```bash
uv run python <skill-dir>/scripts/find_codex_session.py <session-name-or-id>
uv run python <skill-dir>/scripts/find_codex_session.py <session-name-or-id> --cwd-hint <expected-path>
uv run python <skill-dir>/scripts/find_codex_session.py <session-name-or-id> --json      # structured output for programmatic picking
```

   - If `uv` is missing, fall back to `python3`, then `python`. If none are available, tell the user "Python 3 required — install from python.org" and stop.
   - If the helper is missing, read `~/.codex/session_index.jsonl` and rglob `~/.codex/sessions/**/*<session-id>*.jsonl`.
   - Each helper supports `--help` for the full flag reference (precedence rules, score weights, edge cases).

3. Pick the most likely candidate.
   - Prefer the candidate with the highest helper score (exact name match > exact id match > id prefix match).
   - On ties, prefer candidates matching the user's cwd/project hint.
   - Still tied? Prefer the most recent `updated_at`.
   - Still ambiguous? Show the top candidates and ask which session to take over.

4. Locate the rollout and auxiliary files.

```bash
SESSION_ID=<found-session-id>
find ~/.codex/sessions -name "*${SESSION_ID}*.jsonl" -print
find ~/.codex/shell_snapshots -name "*${SESSION_ID}*" -print
```


5. Read the rollout in bounded windows. Do not dump whole JSONL lines — one line can be several KB.
   - Capture: original goal, mid-session scope changes, created/modified files, commits, failed tests, errors, last user request, last tool result, cwd changes, thread rename.
   - Read **head first** (goal + session_meta + initial turns), **then tail** (recent state). Skip the middle unless you need a pivot.

```bash
ROLLOUT=<found-rollout-jsonl>
uv run python <skill-dir>/scripts/summarize_codex_rollout.py "$ROLLOUT" --tail 0 --max-events 40     # head: goal, session_meta, initial turns
uv run python <skill-dir>/scripts/summarize_codex_rollout.py "$ROLLOUT" --tail 120                    # tail: recent state
uv run python <skill-dir>/scripts/summarize_codex_rollout.py "$ROLLOUT" --from-line <N> --max-events 80   # pivot: specific error/rename line
```

   - `--limit` caps chars per event (default 700). If a tool output looks truncated mid-diff or mid-error, re-run the same window with `--limit 2000` to see it in full.
   - If the helper is missing, use `rg -n --only-matching` for line numbers and short matches, then open only small surrounding windows. Never print entire JSONL matches.

6. Re-verify the actual working directory state.
   - Do not trust the rollout for package installs, created files, test results, branch, or git status.
   - From the actual cwd (confirmed via `session_meta` or the latest `turn_context`), run:

```bash
git status --short
git log --oneline -15
git worktree list
```

   - If it is not a git repo, say so and verify via the filesystem instead.

7. Before changing any files, build a short handoff summary:
   - Session name and Codex session id
   - Rollout path and shell snapshot path
   - Actual working directory and branch
   - The user's original goal
   - What is already done
   - The blocking point and any open risks
   - The first command or file to resume with

8. Only resume work from a verified state.
   - If the next step is clear and low-risk, proceed after presenting the handoff summary.
   - If the state is ambiguous, do not guess — ask the decision you need.

## Notes

- Do not record the latest state of a specific session into `CLAUDE.md`, `AGENTS.md`, or this skill.
- Do not revert or overwrite changes in the actual working directory unless the user explicitly asks.
- Treat created files, installed packages, and test results as stale until re-confirmed on disk.
