#!/usr/bin/env bash
# Shared helpers for the smoke-test gate. Sourced by .claude/hooks/require-smoke-test.sh
# and .claude/skills/smoke-test/smoke.sh; not a hook on its own.
#
# A "runtime change" is any changed .py file (or requirements.txt) that can affect what the
# API serves: everything except tests, scripts, migrations and Claude config. Changes are
# measured against the merge-base with the default branch and include uncommitted and
# untracked files. The fingerprint hashes the *content* of those files, so committing does
# not invalidate a smoke run but editing runtime code does. Which endpoints must be checked is
# decided by smoke_routes.py (call graph from route handlers to the changed functions).

SMOKE_RUNTIME_INCLUDE='(\.py$|^requirements\.txt$)'
SMOKE_RUNTIME_EXCLUDE='^(tests|scripts|alembic|docs|\.claude|\.github|venv)/|(^|/)test_[^/]*\.py$|(^|/)conftest\.py$'

# smoke_base <repo> -> merge-base commit with the default branch, or empty if none is known
smoke_base() {
  local repo=$1 ref
  for ref in origin/HEAD origin/main main origin/master master; do
    if git -C "$repo" rev-parse -q --verify "$ref^{commit}" >/dev/null 2>&1; then
      git -C "$repo" merge-base HEAD "$ref" 2>/dev/null && return 0
    fi
  done
  return 0
}

# smoke_changed_files <repo> -> sorted runtime files changed vs base (committed, staged, unstaged, untracked)
smoke_changed_files() {
  local repo=$1 base
  base=$(smoke_base "$repo")
  {
    if [ -n "$base" ]; then
      git -C "$repo" diff --name-only "$base" --
    elif git -C "$repo" rev-parse -q --verify HEAD >/dev/null 2>&1; then
      git -C "$repo" diff --name-only HEAD --
    fi
    git -C "$repo" ls-files --others --exclude-standard
  } 2>/dev/null | grep -E "$SMOKE_RUNTIME_INCLUDE" | grep -vE "$SMOKE_RUNTIME_EXCLUDE" | sort -u
}

# smoke_fingerprint <repo> <changed-files> -> sha256 over "path<TAB>blob" of each changed file
smoke_fingerprint() {
  local repo=$1 files=$2 f blob
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    if [ -f "$repo/$f" ]; then blob=$(git -C "$repo" hash-object -- "$f"); else blob=DELETED; fi
    printf '%s\t%s\n' "$f" "$blob"
  done <<<"$files" | sha256sum | cut -c1-16
}

smoke_state_dir() { printf '%s/.claude/smoke-state' "$1"; }

# smoke_verdict <state-file> -> pass | waived | failing | empty | missing | malformed
smoke_verdict() {
  local state=$1
  [ -f "$state" ] || { echo missing; return; }
  jq -r '
    if type != "object" then "malformed"
    elif (.waiver | type) == "object" and ((.waiver.reason // "") | length) > 0 then "waived"
    elif ((.checks // []) | length) == 0 then "empty"
    elif all(.checks[]; .pass == true) then "pass"
    else "failing" end
  ' "$state" 2>/dev/null || echo malformed
}

# smoke_routes <repo> <changed-files> [state-file] -> JSON from smoke_routes.py: the routes whose
# handlers reach the changed code, which of them a passing check covers, and whether enough are.
smoke_routes() {
  printf '%s\n' "$2" | python3 "$(dirname "${BASH_SOURCE[0]}")/smoke_routes.py" \
    --repo "$1" --base "$(smoke_base "$1")" --exclude "$SMOKE_RUNTIME_EXCLUDE" --state "${3:-}"
}
