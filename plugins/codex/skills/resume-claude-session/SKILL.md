---
name: resume-claude-session
description: Use when the user asks Codex to continue, resume, or take over work from a Claude Code session by session name or session id (UUID or UUID prefix). Examples include "continue the claude session called feature-x" or "/resume-claude-session a3b5c9f2". Do not use for ordinary Codex resume commands or when the user only asks about Codex's own session history.
---

# Resume Claude Code session

## Purpose

When Codex takes over work from a Claude Code session, reconstruct the current state by reading both the Claude storage and the actual working directory. Do not hand off based on only the last few conversation lines.

## Procedure

1. Determine the query.
   - Input is a session **name** (e.g. `feature-x`) or a **session id** (UUID or UUID prefix, e.g. `a3b5c9f2`). Optionally a working-directory or project hint from the user.
   - If the query looks like a UUID (8+ hex, dashes allowed), go straight to the id lookup path — it matches on the filesystem and works even when the session is not in the live index.
   - Otherwise, use the name path. `claude feature-x` means `feature-x`.
   - If several names are plausible, confirm briefly once before a wider search.

2. Find session candidates.

```bash
uv run python <skill-dir>/scripts/find_claude_session.py <session-name-or-id>
uv run python <skill-dir>/scripts/find_claude_session.py <session-name-or-id> --cwd-hint <expected-path>
uv run python <skill-dir>/scripts/find_claude_session.py <session-name-or-id> --json      # structured output for programmatic picking
```

   - If `uv` is missing, fall back to `python3`, then `python`. If none are available, tell the user "Python 3 required — install from python.org" and stop.
   - If the helper is missing, `rg` `"(customTitle|agentName)":"<name>"` over `~/.claude/projects ~/.claude/history.jsonl`, or for a UUID, rglob `~/.claude/projects/**/<uuid>.jsonl`.
   - Each helper supports `--help` for the full flag reference (precedence rules, score weights, edge cases).

3. Pick the most likely candidate.
   - Prefer the candidate with the highest helper score (exact name match > exact id match > id prefix match).
   - On ties, prefer candidates matching the user's cwd/project hint.
   - Still tied? Prefer the most recent `updatedAt` or timestamp.
   - Still ambiguous? Show the top candidates and ask which session to take over.

4. Locate the transcript and task files.

```bash
SESSION_ID=<found-session-id>
find ~/.claude/projects -name "${SESSION_ID}.jsonl" -print
find ~/.claude/tasks/"${SESSION_ID}" -maxdepth 1 -type f | sort -V
```

5. Skim the task files before the transcript.
   - Task files usually show the work map and open points faster than the transcript.
   - Read enough to describe the goal, artifacts, blocking point, and modified files.

```bash
for f in ~/.claude/tasks/"${SESSION_ID}"/*.json; do
  printf '\n## %s\n' "$f"
  sed -n '1,160p' "$f"
done
```

6. Read the transcript in bounded windows. Do not dump whole JSONL lines — one line can be several KB.
   - Capture: original goal, mid-session scope changes, created/modified files, commits, failed tests, errors, last user request, last tool result, cwd changes.
   - Read **head first** (goal + initial user turn + cwd), **then tail** (recent state). Skip the middle unless you need a pivot.

```bash
TRANSCRIPT=<found-transcript-jsonl>
uv run python <skill-dir>/scripts/summarize_claude_transcript.py "$TRANSCRIPT" --tail 0 --max-events 40     # head: goal, first user message, initial cwd
uv run python <skill-dir>/scripts/summarize_claude_transcript.py "$TRANSCRIPT" --tail 120                   # tail: recent state
uv run python <skill-dir>/scripts/summarize_claude_transcript.py "$TRANSCRIPT" --from-line <N> --max-events 80   # pivot: specific error/turn
```

   - `--limit` caps chars per event (default 700). If a tool result or assistant message looks truncated mid-diff or mid-error, re-run the same window with `--limit 2000`.
   - If the helper is missing, use `rg -n --only-matching` for line numbers and short matches, then open small windows with `sed -n '<start>,<end>p'`. Never print entire JSONL matches.

7. Re-verify the actual working directory state.
   - Do not trust the transcript for package installs, created files, test results, branch, or git status.
   - From the actual cwd (confirmed from the transcript), run:

```bash
git status --short
git log --oneline -15
git worktree list
```

   - If it is not a git repo, say so and verify via the filesystem instead.

8. Before changing any files, build a short handoff summary:
   - Session name and `sessionId`
   - Actual working directory and branch
   - The user's original goal
   - What is already done
   - The blocking point and any open risks
   - The first command or file to resume with

9. Only resume work from a verified state.
   - If the next step is clear and low-risk, proceed after presenting the handoff summary.
   - If the state is ambiguous, do not guess — ask the decision you need.

## Notes

- Do not record the latest state of a specific session into `AGENTS.md` or this skill.
- Do not revert or overwrite changes in the actual working directory unless the user explicitly asks.
- Treat created files, installed packages, and test results as stale until re-confirmed on disk.
