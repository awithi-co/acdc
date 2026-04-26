---
name: recall-context
description: Use when the user asks Claude to find content in past sessions by topic or free-text query. Examples include "where did we decide on the ACDC name", "where did I just decide that", "where did we discuss caching last week". For lookup by session name or UUID, use recall-session. For taking over a Codex session, use resume-codex-session.
---

# Recall Claude context

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
   - (b) → `--current` (helper reads the full JSONL on disk, including pre-compact events)
   - (c) → `--days N`
   - (d) → `--since YYYY-MM-DD`

2. Resolve the current transcript path:
   - Try `$CLAUDE_TRANSCRIPT_PATH`
   - Otherwise: read `~/.claude/sessions/<pid>.json` and construct `~/.claude/projects/<escaped-cwd>/<session-uuid>.jsonl` (`<escaped-cwd>` = `pwd` with `/` → `-`)
   - Fallback: latest-mtime `*.jsonl` under `~/.claude/projects/<escaped-cwd>/`

3. Run the helper.

```bash
uv run python <skill-dir>/scripts/grep_recall.py \
    --query "<USER_TOPIC>" \
    --current-transcript "$TRANSCRIPT_PATH" \
    <SCOPE_FLAG>
```

   - For broader scope, add `--cwd-hint $(pwd)` to bias toward the current project.
   - Use `--include-tools` when the user wants to find content in tool calls (e.g., "where did I use that git command").

4. Present results.
   - 0 matches: report "no matches"; suggest broader scope, different terms, or `--include-tools`.
   - 1+ matches: each segment with `★`-marked hit lines and a short paraphrase. Quote the matched preview verbatim.

5. Offer follow-ups:
   - "Show full segment N" → `summarize_claude_transcript.py --from-line=L<seg.lo> --tail=L<seg.hi>`
   - "Related term" → re-run `grep_recall.py`
   - "That whole session" → switch to `recall-session` with the resolved UUID

## Notes

- The helper parses each event's text content; UUIDs and metadata are excluded by design.
- `--include-tools` truncates each tool input to 500 chars.
