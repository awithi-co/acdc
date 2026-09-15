#!/usr/bin/env bash
# okf_check.sh — validate an OKF bundle, or render its graph.
#
# Usage:
#   okf_check.sh <bundle-dir> [--strict] [--json]
#   okf_check.sh <bundle-dir> --graph      # also writes <bundle>/okf-graph.html
#
# Exit codes: 0 pass · 1 fail · 2 usage/environment error.
#
# The vendored validator walks a directory. It knows nothing about .gitignore,
# so pointed at a working repo it would count scratch files, local-only notes
# and deliberately-malformed test fixtures as part of the bundle. This script
# decides the bundle's file set once — from git when the bundle lives in a
# repository, from the filesystem when it does not — copies exactly that set
# into a temp tree, and validates there. Validation and --graph therefore always
# see the same set of files.
#
# Excluded from a git-backed bundle:
#   - files git ignores, and files marked skip-worktree / assume-unchanged
#     (those are local-only variants, not shared knowledge)
#   - deleted-but-still-staged paths
#
# The validator writes okf-report.json next to the bundle it is given; because
# that is the temp tree, this script never litters the real bundle. Only
# --graph copies anything back.
#
# Requires: node (https://nodejs.org). git is optional.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VENDOR="$SCRIPT_DIR/vendor"

MODE=validate
BUNDLE=""
PASSTHRU=()
for arg in "$@"; do
  case "$arg" in
    --graph) MODE=graph ;;
    --*)     PASSTHRU+=("$arg") ;;
    *)       [ -n "$BUNDLE" ] || BUNDLE="$arg" ;;
  esac
done

if [ -z "$BUNDLE" ]; then
  echo "usage: okf_check.sh <bundle-dir> [--strict] [--json] [--graph]" >&2
  exit 2
fi
if [ ! -d "$BUNDLE" ]; then
  echo "okf_check: not a directory: $BUNDLE" >&2
  exit 2
fi
if ! command -v node >/dev/null 2>&1; then
  echo "okf_check: node is required — install from https://nodejs.org" >&2
  exit 2
fi

TOOL="$VENDOR/okf-$([ "$MODE" = graph ] && echo graph || echo validate).mjs"
if [ ! -f "$TOOL" ]; then
  echo "okf_check: vendored validator missing: $TOOL" >&2
  exit 2
fi

BUNDLE="$(cd -- "$BUNDLE" && pwd)"
# The staged copy keeps the bundle's own directory name: the validator reports
# under `path.basename(bundleDir)`, and a temp name there reads as a bug.
STAGE_PARENT="$(mktemp -d)"
STAGE="$STAGE_PARENT/$(basename -- "$BUNDLE")"
mkdir -p "$STAGE"
trap 'rm -rf "$STAGE_PARENT"' EXIT

copy_into_stage() {
  # reads NUL-separated paths relative to $BUNDLE on stdin
  while IFS= read -r -d '' rel; do
    rel="${rel#./}"
    [ -f "$BUNDLE/$rel" ] || continue
    mkdir -p "$STAGE/$(dirname -- "$rel")"
    cp -- "$BUNDLE/$rel" "$STAGE/$rel"
  done
}

if command -v git >/dev/null 2>&1 && git -C "$BUNDLE" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  {
    # `ls-files -v` prefixes each path with its status letter. Uppercase = the
    # normal cache states we want; lowercase (h, s, …) marks assume-unchanged /
    # skip-worktree, i.e. a file whose local copy is intentionally private.
    git -C "$BUNDLE" ls-files -v -z -- '*.md' | while IFS= read -r -d '' line; do
      case "${line:0:1}" in
        H|M|C|R|K) printf '%s\0' "${line:2}" ;;
      esac
    done
    git -C "$BUNDLE" ls-files -o --exclude-standard -z -- '*.md'
  } | copy_into_stage
else
  (cd -- "$BUNDLE" && find . -name '*.md' -type f -not -path '*/.*' -print0) | copy_into_stage
fi

# PIPESTATUS must survive the pipeline, so `set -e`/pipefail must not abort on a
# validator exit code of 1 — that is a finding, not a crash.
set +e +o pipefail
# The `wrote …` lines name files inside the temp tree that the caller will never
# see; --graph announces the one artifact that does survive, below.
node "$TOOL" "$STAGE" ${PASSTHRU[0]+"${PASSTHRU[@]}"} \
  | sed -e "s#$STAGE#$BUNDLE#g" -e '/^  wrote /d'
STATUS="${PIPESTATUS[0]}"
set -e -o pipefail

if [ "$MODE" = graph ] && [ -f "$STAGE/visualize.html" ]; then
  cp -- "$STAGE/visualize.html" "$BUNDLE/okf-graph.html"
  echo "→ $BUNDLE/okf-graph.html"
fi

exit "$STATUS"
