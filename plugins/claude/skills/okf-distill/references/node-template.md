# Node templates

One example per node type. A distilled node is short on purpose: body ≤ 25
lines. If it needs more, it is two nodes.

Links are written **file-relative** — `../concepts/x.md` from a sibling
directory, `x.md` within the same one. OKF v0.2 §6.1 resolves both that form
and the root-relative `/concepts/x.md`, but Obsidian's graph view resolves only
the file-relative one, so a bundle written the other way opens there with every
edge missing. `okf_node.py` accepts either form in the JSON spec and converts on
write, so a spec can keep using the root-relative form it finds easier to
reason about.

The rule that governs all three: **a node records a decision, a lesson, or a
definition — never a current state.** "The migration is half done" is stale the
day after it is written. "We split the migration in two because the index
rebuild locks the table for 40 minutes" stays true.

---

## Decision

What was decided, why, and what was rejected. Without the rejected alternatives
the node cannot stop the same debate from restarting.

```markdown
---
type: "Decision"
status: "draft"
title: "Vendor the OKF validator instead of depending on it"
sources: ["SHARE-260916"]
distilled_at: "2026-09-16"
---

# Vendor the OKF validator instead of depending on it

**Decision.** Ship a copy of `okf-validate.mjs` and `okf-graph.mjs` inside the
skill, under `scripts/vendor/`, with the upstream MIT licence and a NOTICE.

**Why.** A skill has to run on a fresh machine with nothing installed. A
plugin that needs a second clone at a fixed path is a skill that fails on first
use.

**Rejected.**
- *Depend on a checkout at `$OKF_HOME`* — works on the author's machine only.
- *npm install at runtime* — the scripts are standard-library-only by design,
  and a network fetch inside a skill is a new failure mode.

**Cost accepted.** Two local patches now live in a copy that upstream does not
know about. Upstreaming them retires this node.

## Related

- [Two validator patches](../concepts/validator-patches.md)
```

---

## Lesson

A rule plus the incident that earned it. A lesson with no evidence is an
opinion, and the next reader will re-litigate it.

```markdown
---
type: "Lesson"
status: "draft"
title: "Distil decisions, never current state"
sources: ["SHARE-260916"]
distilled_at: "2026-09-16"
---

# Distil decisions, never current state

**Rule.** A knowledge node records what was decided, learned, or defined. It
never records where the work currently stands.

**Evidence.** Three attempts to keep a "current state" page accurate scored
1/8, 1/8 and 1/7 on a later audit: every factual claim about progress had gone
stale, while the decisions recorded beside them were all still true.

**How to apply.** When a candidate node starts with "we are now" or "the
remaining work is", either drop it or rewrite it as the decision that produced
that state.
```

---

## Concept

A thing or term that the work defined. The node exists so later readers use
the word the same way.

```markdown
---
type: "Concept"
status: "draft"
title: "Bundle root"
sources: ["SHARE-260916"]
distilled_at: "2026-09-16"
---

# Bundle root

The directory an OKF bundle starts from: the one whose `index.md` frontmatter
carries `okf_version` and nothing else. Link targets beginning with `/` resolve
against it; every other `.md` link resolves against the linking file.

It matters because it is the only place in a bundle where `index.md` is allowed
to have frontmatter at all. Every other `index.md` and every `log.md` must have
none.

## Related

- [Where the bundle root is recorded](acdc-okf-json.md)
```
