---
name: okf-write
description: Use when the user asks to write, add, or fix a document in a knowledge bundle that follows the Open Knowledge Format (OKF), or asks to validate one. Examples include "add a doc about the retry policy to the knowledge base", "make these notes OKF-conformant", "check the bundle". To turn past agent sessions into knowledge nodes, use okf-distill.
---

# Write OKF documents

Open Knowledge Format (OKF) v0.2 is a convention for a directory of markdown
files that an agent can read as a knowledge graph. Frontmatter carries the
metadata; `.md` links in the body are the edges.

## Procedure

1. Locate the bundle root.

```bash
cat "$(git rev-parse --show-toplevel 2>/dev/null || pwd)/.acdc/okf.json"
```

   - The file holds one key: `{"bundle": "<path relative to repo root>"}`.
   - If it does not exist, ask the user once where the bundle lives (or where it
     should live), then write the file so the next session does not ask again:

```bash
mkdir -p .acdc && printf '{"bundle": "%s"}\n' "docs/knowledge" > .acdc/okf.json
```

   - A new bundle root needs an `index.md` whose frontmatter carries
     `okf_version: "0.2"` and nothing else.

2. Read before writing. List the bundle, read the root `index.md` and two or
   three neighbouring documents. Match their `type` vocabulary, directory
   layout and linking style rather than inventing a parallel one.

3. Write the document.

   - Every concept document opens with YAML frontmatter. `type` is required;
     include `status` as well (`draft`, `stable`, or `deprecated`). Any other
     field you need is allowed — OKF ignores keys it does not know.

```markdown
---
type: Decision
status: draft
---

# Retry policy for the ingest worker

...body...
```

   - Filenames are lowercase kebab-case and end in `.md`.
   - Link related documents with `.md` links in the body. A leading `/` resolves
     from the bundle root; anything else resolves relative to the linking file.
     A link is the edge — a document nothing links to is an orphan.
   - **Never put frontmatter on a non-root `index.md` or on any `log.md`.**
     Those are reserved files: an index is a listing, not a concept. Frontmatter
     there is a hard conformance error (M4). Only the bundle-root `index.md` may
     carry frontmatter, and only `okf_version`.
   - Add the new document to the listing in its directory's `index.md`.

4. Validate, and fix what it reports.

```bash
<skill-dir>/scripts/okf_check.sh <bundle>
<skill-dir>/scripts/okf_check.sh <bundle> --strict      # warnings are fatal
<skill-dir>/scripts/okf_check.sh <bundle> --graph       # writes <bundle>/okf-graph.html
```

   - Errors (`M*`) are conformance failures — fix every one before reporting.
   - Warnings (`S*`) are quality lint: unresolved links, orphan concepts, an
     index that does not list a sibling. Fix the ones your change caused.
   - Requires `node` on PATH. The validator is vendored under `scripts/vendor/`;
     nothing is downloaded and nothing leaves the machine.

5. Report what you wrote, the links you added, and the validator summary
   (concept count, errors, warnings). Do not claim conformance you did not run.

## Notes

- The checker scopes the bundle to what git tracks when the bundle is inside a
  repository, so ignored and local-only files are never counted.
- `okf_check.sh` writes nothing into the bundle except `okf-graph.html` under
  `--graph`.
- To distill past Claude Code or Codex sessions into bundle nodes, use
  `okf-distill`.
