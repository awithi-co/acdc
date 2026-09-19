# ACDC

> Part of the [AWITHI](https://github.com/awithi-co) family — *AI with AI*. ACDC = **A**gent **C**ontext **D**elivery **C**hannel — AC ↔ DC, context flowing both ways.

**Read this in:** English · [한국어](README.ko.md)

The context of a coding-agent session — what you decided, what's already tried, where you left off — is the most valuable thing in the room. It's also fragile: rate limits stop you mid-flow, compaction leaves the agent foggy on earlier turns, and the trail goes cold across days.

ACDC reads your local Claude Code and Codex sessions to put the context back where you need it.

## What ACDC does

| Skill | What it does | Example invocation |
|-------|--------------|--------------------|
| `resume-codex-session` (Claude side) / `resume-claude-session` (Codex side) | Cross-agent handoff: read the prior session and current repo state, then hand a structured summary to the new agent so it continues rather than echoes the last few messages. | "resume the codex session called api-refactor" |
| `recall-session` | Self-recall: look up your own past session by name or UUID. Returns a timeline summary — what you decided, what changed, where you left off. | "show me yesterday's api-refactor session" |
| `recall-context` | Topic search: find where you discussed something across your sessions, even pre-compaction in the current one. | "where did we decide on the ACDC name" |

Five skills per agent, each plugin self-contained.

## Direct agent messaging (experimental)

The [ACDC stdio relay](RELAY.md) lets Codex discover running Claude recipients and send context through Claude channel notifications. Claude delegates work to Codex through separately installed Codex tools; ACDC does not provide reverse delivery or require a remote Codex runtime. ACDC stores no message inbox or history. The MCP runtime requires uv/Python 3.11+; existing session-reading skills remain independent.

## Knowledge: OKF

Recall is continuity within a session's lifetime. Some of what a session produces should outlive it — the choice you made and the two you rejected, the rule an incident earned, the term the work defined. Two skills write those into an [Open Knowledge Format](https://github.com/GoogleCloudPlatform/knowledge-catalog) (OKF) v0.2 bundle: a directory of markdown files whose frontmatter is the metadata and whose `.md` links are the graph edges.

| Skill | What it does | Example invocation |
|-------|--------------|--------------------|
| `okf-write` | Author or fix one document as OKF, then validate the bundle. | "add a doc about the retry policy to the knowledge base" |
| `okf-distill` | Read finished Claude Code and Codex sessions and write what should outlive them as nodes. | "distill the api-refactor session into the knowledge base" |

**Node types — three, and no fourth.**

| Type | Records | Must contain |
|------|---------|--------------|
| `Decision` | A choice the work made | what was decided, why, which alternatives were rejected |
| `Lesson` | A rule the work earned | the rule, and the incident that proves it |
| `Concept` | A thing or term the work defined | the definition, and why it matters |

There is deliberately no "current state" node. A record of where the work stands is false within days; the decision that produced it stays true. `okf-distill` rewrites such candidates as the decision behind them, or drops them.

**Provenance.** Each node records where it came from, in the fields OKF v0.2 §5 names: `sources` entries carrying a required `resource` URI — a session becomes `claude-session://<name-or-id>` or `codex-session://<name-or-id>` — and `generated: { by, at }`. `okf_node.py` writes both, so a node can always be traced back to the session it came from.

**Names.** The three roles are fixed; their names follow the bundle. A knowledge base that already calls its lessons `Finding` keeps doing so — the skill reads the existing documents first and only an empty bundle gets the default `Decision` / `Lesson` / `Concept`. Links are written file-relative so that Obsidian's graph view resolves them too; OKF accepts both forms, Obsidian only that one.

**Where the bundle lives.** `.acdc/okf.json` at the repo root holds one key:

```json
{"bundle": "docs/knowledge"}
```

Both skills read it, and write it once (after asking) when it is missing.

**Validation.** `okf-write/scripts/okf_check.sh <bundle> [--strict] [--graph]` runs a vendored copy of the [okf-conformance](https://github.com/Sudhakaran88/okf-conformance) validator (MIT © WitsCode, `scripts/vendor/`), so nothing is downloaded and nothing leaves the machine. It requires `node`; the rest of ACDC does not. The bundle's file set comes from git when the bundle sits in a repository, so ignored and local-only files are never counted. `--graph` writes `<bundle>/okf-graph.html`, a self-contained interactive view.

`okf-distill` reads transcripts, which are not sanitized. The skill strips hostnames, addresses, personal names, internal URLs and credentials before writing a node, and drops a node it cannot write without one — but a bundle you intend to publish deserves a read-through.

## Install

**Claude Code** — slash commands inside the CLI:

```
/plugin marketplace add https://github.com/awithi-co/acdc
/plugin install acdc
```

**Codex** — shell command + enable:

```
codex plugin marketplace add awithi-co/acdc
```

`marketplace add` registers ACDC as *available*. To activate it (so skills are discoverable by natural-language dispatch), either pick ACDC from `codex`'s interactive plugin picker, or append to `~/.codex/config.toml`:

```toml
[plugins."acdc@acdc"]
enabled = true
```

After install, trigger skills with natural language — the example invocations above work as-is. The plugin's manifest routes the request to the correct skill in each agent.

## How it works

Each skill ships two Python helpers:

- a **finder** that scores local session candidates by name, cwd hint, and recency
- a **bounded summarizer** that reconstructs the timeline from the transcript or rollout JSONL

For **handoff**, the skill cross-checks the reconstructed state against the current `git status`, branch, and worktree before producing the summary. The new agent acts on the handoff, not on raw transcript content.

For **recall by identifier**, the same finder/summarizer pair is used without the repo cross-check (the agent already has its own context).

For **topic search**, a `grep_recall.py` helper scans JSONL files (current session + recent partition) and returns matched segments with surrounding event context.

The session-reading helpers use the Python 3 standard library, require no network access, and remain read-only against session storage. The optional messaging runtime has separate SDK dependencies and sends messages to running chats.

## Session storage layout

ACDC only reads these paths; it never writes to them.

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

## Requirements

Python 3 on PATH. Pre-installed on macOS 12.3+ and most Linux distributions; on Windows, install from python.org if unavailable. The skill tries `uv run python`, then `python3`, then `python`, and stops with a clear message if none are found.

The OKF bundle checker additionally needs [Node.js](https://nodejs.org) on PATH. Only `okf_check.sh` uses it; the recall and handoff skills do not.

## Privacy & limitations

- ACDC reads local session files on your machine to build a handoff or recall summary. It does **not** upload anything to an external service, and it does **not** sanitize or redact transcript content.
- Each plugin needs local filesystem access to its own session storage (and to the other agent's storage when used for handoff).
- Session storage formats are owned by their respective products; large version changes may require parser updates.
- When multiple sessions match the same name, the skill shows scored candidates and asks.
- If the original session's working directory was moved or deleted, repo-state verification falls back to filesystem checks.

## Contributors

Each plugin is self-contained: `finder + summarizer` scripts are duplicated rather than symlinked, on the assumption that plugin install ships a directory tree.

> When fixing a bug in `find_*_session.py` or `summarize_*.py`, mirror the fix to the corresponding `recall-session/scripts/` copy. Future work: extract shared helpers to a `_vendor/` location.

## License

MIT
