---
name: recall-context
description: Use when the user asks Codex to find content in past rollouts by topic or free-text query. Examples: "find where we decided on the ACDC name", "where did I just decide that", "find the testing strategy discussion". For lookup by session name or UUID, use recall-session. For taking over a Claude session, use resume-claude-session.
---

# Recall Codex context

## Procedure

1. Ask the user for scope BEFORE running anything:

   ```
   What scope?
     (a) Current session only
     (b) Pre-compact (= the earlier part of this session)
     (c) Last N days (default 7, customizable)
     (d) Since a specific date
   ```

   Map:
   - (a) → `--current`
   - (b) → `--current`
   - (c) → `--days N`
   - (d) → `--since YYYY-MM-DD`

2. Resolve the current rollout path:
   - Try `$CODEX_ROLLOUT_PATH`
   - Fallback: latest-mtime file under `~/.codex/sessions/**/rollout-*.jsonl`

3. Run the helper:

```bash
uv run python <skill-dir>/scripts/grep_recall.py \
    --query "<USER_TOPIC>" \
    --current-transcript "$ROLLOUT_PATH" \
    <SCOPE_FLAG>
```

   - `--include-tools` extends search into `function_call` arguments.
   - `--cwd-hint` is silently ignored on Codex (partitions by date, not cwd).

4. Present results.
   - 0 matches: report "no matches"; suggest broader scope or `--include-tools`.
   - 1+ matches: each segment with `★` hits and a short paraphrase.

5. Offer follow-ups: `summarize_codex_rollout.py --from-line --tail` for full segment; related term re-run; `recall-session` for the whole rollout.

## Notes

- The helper parses each event's text content; UUIDs and metadata are excluded by design.
- `--include-tools` truncates each tool input to 500 chars.
