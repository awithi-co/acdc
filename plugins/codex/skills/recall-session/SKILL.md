---
name: recall-session
description: Use when the user asks Codex to look up one of its own past rollouts by name or UUID and summarize it. Examples include "show me the codex session feature-x", "show me rollout 019dc08e", "summarize the acdc rename session". For free-text topic search, use recall-context. For taking over a Claude session, use resume-claude-session.
---

# Recall Codex session

## Procedure

1. Classify the input.
   - 8+ hex chars matching `^[0-9a-f-]+$` → UUID
   - Otherwise → name string

2. Find candidates.

```bash
uv run python <skill-dir>/scripts/find_codex_session.py <name-or-id>
uv run python <skill-dir>/scripts/find_codex_session.py <name-or-id> --cwd-hint <path>
uv run python <skill-dir>/scripts/find_codex_session.py <name-or-id> --json
```

   - Falls back to `python3`/`python` if `uv` is missing.

3. Pick the best candidate: score → cwd-hint match → newest `updated_at`. Surface multiple if scores tie closely.

4. Summarize. Map segments first — a rollout can hold several working sessions (multi-day thread) plus an `inherited replay` prefix copied in when the thread was forked; only live segments are this session's own work.

```bash
ROLLOUT=<found-rollout-jsonl>
uv run python <skill-dir>/scripts/summarize_codex_rollout.py "$ROLLOUT" --segments
uv run python <skill-dir>/scripts/summarize_codex_rollout.py "$ROLLOUT" --segment last
uv run python <skill-dir>/scripts/summarize_codex_rollout.py "$ROLLOUT" --segment <N>
uv run python <skill-dir>/scripts/summarize_codex_rollout.py "$ROLLOUT" --from-line <N> --max-events 80
```

5. Present a natural-language recall: when, main topics, files changed, final state.

6. Offer follow-ups: expand a section, `recall-context` for related topics, or `git log --since=<ts>` from the session cwd.

## Notes

- Self-recall only. No repo cross-check, no handoff bundle.
- For cross-agent take-over (Claude continuing Codex's work), use `resume-claude-session`.
