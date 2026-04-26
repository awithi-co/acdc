# ACDC

> Part of the [AWITHI](https://github.com/awithi-co) family — *AI with AI*. AC ↔ DC, context flowing both ways.

**Read this in:** English · [한국어](README.ko.md)

Continue local coding-agent sessions across Claude Code and Codex. When you hit a wall in one agent and want the other to pick up the work, ACDC reads the prior local session files and the current repo state, then hands a structured summary to the new agent so it can continue — not just echo the last few messages.

## Supported directions

- **Claude Code** takes over a **Codex** session
- **Codex** takes over a **Claude Code** session

## Requirements

Python 3 on PATH. Pre-installed on macOS 12.3+ and most Linux distributions; on Windows, install from python.org if unavailable. The skill tries `uv run python`, then `python3`, then `python`, and stops with a clear message if none are found.

## Install (Claude Code)

```
/plugin marketplace add https://github.com/awithi-co/acdc
/plugin install acdc
```

Then invoke the skill with a session name or id:

```
/resume-codex-session <session-name>
/resume-codex-session <session-id>
```

For example:

```
/resume-codex-session api-refactor       # a name, if you set one with /rename
/resume-codex-session 019dc08e           # a UUID (prefix is fine)
```

## Install (Codex)

```
/plugin marketplace add https://github.com/awithi-co/acdc
/plugin install acdc
```

Then invoke the skill with a session name or id:

```
/resume-claude-session <session-name>
/resume-claude-session <session-id>
```

For example:

```
/resume-claude-session api-refactor      # a name, if you set one with /rename
/resume-claude-session a3b5c9f2          # a UUID (prefix is fine)
```

## How it works

Each direction ships a skill with two Python helpers:

- a session finder that scores local session candidates by name, cwd hint, and recency
- a bounded summarizer that reconstructs the timeline from the transcript or rollout JSONL

The skill then cross-checks the reconstructed state against the current `git status`, branch, and worktree before producing a handoff summary. The new agent acts on the handoff, not on raw transcript content.

Python 3 standard library only. No network access. Read-only against session storage.

## Session storage layout

Knowing where each agent keeps its data makes the handoff traceable. ACDC only reads these paths; it never writes to them.

### Claude Code — `~/.claude/`

```
projects/<escaped-cwd>/<session-uuid>.jsonl   transcript (full event stream)
sessions/<pid>.json                           live session metadata (name, cwd, status)
tasks/<session-uuid>/N.json                   structured todos
history.jsonl                                 per-prompt log
```

- Sessions are identified by UUID. The transcript file is named `<uuid>.jsonl`, and its parent directory encodes the original working directory (slashes replaced with dashes).
- Session rename events live inside the transcript as `custom-title` records. Live sessions also expose the current name via `~/.claude/sessions/<pid>.json`.
- Tasks are stored as separate JSON files (`{id, subject, description, activeForm, status}`), one per todo item.

### Codex — `~/.codex/`

```
sessions/YYYY/MM/DD/rollout-<timestamp>-<uuid>.jsonl   rollout (event stream, source of truth)
session_index.jsonl                                    {id, thread_name, updated_at} pointer file
shell_snapshots/<...>                                  per-session shell state
history.jsonl                                          per-prompt log
```

- Sessions are identified by UUID. Rollout files are partitioned by date, not cwd.
- `session_index.jsonl` is an accelerator for name lookup. It is not guaranteed to contain every rollout on disk, so the rollout tree is the source of truth.
- Todos and plans are not separate files. They live inline in the rollout as `function_call` events (`update_plan`, `TodoWrite`).

## Privacy model

ACDC reads local Claude Code and Codex session files on your machine to build a handoff for the current agent. It does not upload anything to an external service, and it does not sanitize or redact transcript content. Use it when you intentionally want one local coding agent to continue work from another.

## Limitations

- Assumes the new agent has local filesystem access to the other agent's session storage.
- Session storage formats are owned by their respective products; large version changes may require parser updates.
- A session picker is needed when multiple candidates match the same name; the skill will show scored candidates and ask.
- If the original session's working directory was moved or deleted, repo-state verification falls back to filesystem checks.

## Skills

ACDC ships three skills per plugin (one cross-agent resume + two self-recall):

| Skill | Purpose | Example invocation |
|-------|---------|--------------------|
| `resume-codex-session` (Claude side) / `resume-claude-session` (Codex side) | Cross-agent handoff: take over the other agent's session by name or UUID | "resume the codex session called api-refactor" |
| `recall-session` | Self-recall: look up your own past session by name or UUID | "show me yesterday's api-refactor session" |
| `recall-context` | Self-recall: free-text topic search across your own current and recent sessions | "where did we decide on the ACDC name" |

The two recall skills share the same finder/summarizer code as the resume skills, plus a new `grep_recall.py` helper that scans JSONL transcripts and returns matched segments with a context window. Each plugin is self-contained — finder/summarizer are duplicated rather than symlinked, on the assumption that plugin install ships a directory tree.

> Contributors: when fixing a bug in `find_*_session.py` or `summarize_*.py`, mirror the fix to the corresponding `recall-session/scripts/` copy. Future work: extract shared helpers to a `_vendor/` location.

## License

MIT
