---
name: okf-distill
description: Use when the user asks to turn past Claude Code or Codex sessions into durable knowledge documents — "distill the api-refactor session into the knowledge base", "extract the decisions from yesterday's sessions as OKF nodes", "write up what we learned in 019dc08e". For summarizing a session back to the user, use recall-session. For authoring a single document by hand, use okf-write.
---

# Distill sessions into OKF nodes

A session summary answers "what happened". A knowledge node answers "what is
true from now on". This skill reads one or more finished sessions and writes
the second kind into an Open Knowledge Format bundle.

## Procedure

1. Establish scope. Ask the user for whatever is missing:
   - which sessions (names, UUIDs, or "the last N"), on which agent;
   - the target bundle. Read it from `.acdc/okf.json` at the repo root
     (`{"bundle": "<relative path>"}`); if absent, ask once and write the file.
   - A new bundle root needs an `index.md` whose frontmatter carries
     `okf_version: "0.2"` and nothing else.

2. Resolve the sessions.

```bash
# Claude Code sessions
uv run python <skill-dir>/../resume-claude-session/scripts/find_claude_session.py <name-or-id> --json

# Codex sessions
uv run python <skill-dir>/../recall-session/scripts/find_codex_session.py <name-or-id> --json
```

   - Fall back to `python3`, then `python`, if `uv` is missing.
   - Pick by highest score, then `--cwd-hint` match, then recency. Ambiguous?
     Show the top candidates and ask.

3. Read the sessions in bounded windows. Read the head for the goal, the tail
   for the outcome, and open the middle only at points the head/tail say matter.

```bash
uv run python <skill-dir>/../resume-claude-session/scripts/summarize_claude_transcript.py "$TRANSCRIPT" --tail 0 --max-events 40
uv run python <skill-dir>/../resume-claude-session/scripts/summarize_claude_transcript.py "$TRANSCRIPT" --tail 120

# Codex rollouts hold more than one working session — map first, then read one.
uv run python <skill-dir>/../recall-session/scripts/summarize_codex_rollout.py "$ROLLOUT" --segments
uv run python <skill-dir>/../recall-session/scripts/summarize_codex_rollout.py "$ROLLOUT" --segment last
```

4. Extract nodes. **You** do this — the scripts only fetch text. Exactly three
   types, and nothing else:

   | Type | Records | Must contain |
   |------|---------|--------------|
   | `Decision` | A choice the work made | what was decided, why, which alternatives were rejected |
   | `Lesson` | A rule the work earned | the rule, and the incident that proves it |
   | `Concept` | A thing or term the work defined | the definition, and why it matters |

   - **Never write a "current state" node.** No "where we are", no "remaining
     work", no status. Those are false within days; the decision that produced
     them stays true. If a candidate node starts with "we are now", rewrite it
     as the decision behind it or drop it.
   - One node per idea. Body ≤ 25 lines. Longer means it is two nodes.
   - Skip anything the session did not actually settle. A debate with no
     conclusion is not a Decision.
   - See `<skill-dir>/references/node-template.md` for one worked example per
     type.
   - **Write in the language of the bundle, not the language of the session.**
     A node joins documents that will be read together; one node in another
     language is the one nobody searches for. Take the language from the
     existing documents; for an empty bundle, take it from the user. A session
     held in one language often belongs in a bundle written in another, so
     decide this before writing, not per node. Quote a term in its original
     language where translating it would lose the meaning.

5. **Sanitize.** A bundle is shared and often published; a transcript is not.
   Before writing, strip internal hostnames and IP addresses, colleague and
   customer names, internal URLs and ticket links, credentials and tokens, and
   absolute paths that carry a username. Describe the role ("the staging host",
   "a teammate") instead. If a node cannot be written without a secret, do not
   write that node.

6. Write each node with the helper, so frontmatter and filenames are consistent.

```bash
echo '{
  "bundle": "<bundle>",
  "subdir": "decisions",
  "type": "Decision",
  "title": "Vendor the OKF validator instead of depending on it",
  "sources": ["<session-name-or-id>"],
  "body": "**Decision.** ...\n\n**Why.** ...\n\n**Rejected.** ...",
  "links": [{"text": "Bundle root", "target": "/concepts/bundle-root.md"}]
}' | uv run python <skill-dir>/scripts/okf_node.py
```

   - The helper sets `type`, `status: draft`, `sources`, `distilled_at`, and a
     kebab-case slug filename; it refuses reserved names (`index`, `log`).
   - Link nodes to each other and to documents that already exist in the
     bundle. An unlinked node is an orphan the graph cannot reach.
   - Links are written **file-relative** (`../concepts/x.md`, or `x.md` within
     the same directory), so both OKF and Obsidian resolve them — Obsidian's
     graph view ignores the root-relative `/concepts/x.md` form and would show
     the bundle with no edges at all. The helper accepts either form in the
     spec and converts on write; hand-written links must be relative.

7. Update the bundle's `index.md` listing so each new node is linked from its
   directory's index. **Non-root `index.md` and `log.md` must carry no
   frontmatter** — that is a hard conformance error (M4).

8. Validate and render the graph.

```bash
<skill-dir>/../okf-write/scripts/okf_check.sh <bundle> --graph
<skill-dir>/../okf-write/scripts/okf_check.sh <bundle>
```

   - Requires `node` on PATH. Fix every error before reporting; fix the
     warnings your own nodes caused.
   - Report the path to `okf-graph.html`. Do not open it — it is a side effect
     of validation, not something the user asked to look at. Open it only if
     they ask:

```bash
xdg-open <bundle>/okf-graph.html            # Linux
open <bundle>/okf-graph.html                # macOS
explorer.exe "$(wslpath -w <bundle>/okf-graph.html)"   # WSL
```

     Under WSL, a browser-devtools tool is the better choice when one is
     available, since it can also read back what rendered.

9. Report: the node titles grouped by type, the validator summary (concepts,
   errors, warnings), the path to `okf-graph.html`, and anything you dropped
   for lack of evidence or because it could not be sanitized.

## Notes

- Sessions are read-only. This skill never writes to session storage.
- Nodes land as `status: draft`. Promotion to `stable` is a human call.
- To author or fix a single document by hand, use `okf-write`.
