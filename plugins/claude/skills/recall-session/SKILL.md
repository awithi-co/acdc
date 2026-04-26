---
name: recall-session
description: Use when the user asks Claude to look up one of its own past sessions by name or UUID and summarize it. Examples: "show me the feature-x session", "/recall-session 019dc08e", "summarize yesterday's api-refactor session". For free-text topic search, use recall-context. For taking over a Codex session, use resume-codex-session.
---

# Recall Claude session

## Procedure

1. Classify the input.
   - 8+ hex chars matching `^[0-9a-f-]+$` → UUID prefix
   - Otherwise → name string

2. Find candidates.

```bash
uv run python <skill-dir>/scripts/find_claude_session.py <name-or-id>
uv run python <skill-dir>/scripts/find_claude_session.py <name-or-id> --cwd-hint <path>
uv run python <skill-dir>/scripts/find_claude_session.py <name-or-id> --json
```

   - Falls back to `python3`/`python` if `uv` is missing.

3. Pick the best candidate: highest score → cwd-hint match → newest `updated_at`. If still ambiguous, show top candidates and ask.

4. Summarize.

```bash
TRANSCRIPT=<found-path>
uv run python <skill-dir>/scripts/summarize_claude_transcript.py "$TRANSCRIPT" --tail 0 --max-events 40
uv run python <skill-dir>/scripts/summarize_claude_transcript.py "$TRANSCRIPT" --tail 120
uv run python <skill-dir>/scripts/summarize_claude_transcript.py "$TRANSCRIPT" --from-line <N> --max-events 80
```

   - `--limit` caps chars per event (default 700); raise to 2000 if a tool output looks truncated.

5. Present a natural-language recall: when the session ran, main topics and decisions, files changed, final state.

6. Offer follow-ups: `--from-line=L<N>` to expand a section, `recall-context` for related topics, or `git log --since=<ts>` from the session cwd.

## Notes

- Self-recall only. No repo cross-check, no handoff bundle.
- For cross-agent take-over (Codex continuing Claude's work), use `resume-codex-session`.
