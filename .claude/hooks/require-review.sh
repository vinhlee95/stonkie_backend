#!/usr/bin/env bash
# PreToolUse hook: block `gh pr create` unless the current HEAD has a passing /multi-review.
# Exit 0 = allow, exit 2 = block (stderr is shown to Claude). Fails closed. No bypass.
set -uo pipefail

REQUIRED_ANGLES='["functionality","architecture","security","scalability","tests"]'

block() {
  printf 'PR creation blocked by require-review hook: %b\n' "$1" >&2
  exit 2
}

input=$(cat)

command -v jq >/dev/null 2>&1 || block "jq is not installed."
command -v git >/dev/null 2>&1 || block "git is not installed."

cmd=$(printf '%s' "$input" | jq -er '.tool_input.command // ""' 2>/dev/null) \
  || block "could not parse hook input."

# bash regex, not `printf | grep -q`: with pipefail a SIGPIPE on printf would read as "no match" and open the gate
# matches `gh pr create|new`, path-qualified gh, global flags (`gh -R o/r pr create`),
# and `gh api` writes to /pulls (-X POST or -f/-F/--input, which make gh api POST)
gh_re='(^|[;&|(`[:space:]])([^[:space:];&|]*/)?gh([[:space:]]+[^[:space:];&|]+)*'
pr_create_re="${gh_re}[[:space:]]+pr[[:space:]]+(create|new)([[:space:]]|\$)"
api_pulls_re="${gh_re}[[:space:]]+api[[:space:]].*/pulls([[:space:]?]|\$)"
api_write_re='(-X|--method)[[:space:]=]*POST|[[:space:]](-f|-F|--field|--raw-field|--input)[[:space:]=]'
if ! [[ "$cmd" =~ $pr_create_re ]]; then
  [[ "$cmd" =~ $api_pulls_re && "$cmd" =~ $api_write_re ]] || exit 0
fi

cwd=$(printf '%s' "$input" | jq -r '.cwd // empty')
[ -n "$cwd" ] || cwd="${CLAUDE_PROJECT_DIR:-$PWD}"

top=$(git -C "$cwd" rev-parse --show-toplevel 2>/dev/null) || block "$cwd is not inside a git repository."
sha=$(git -C "$top" rev-parse HEAD 2>/dev/null) || block "could not resolve HEAD in $top."

if [ -n "$(git -C "$top" status --porcelain --untracked-files=no)" ]; then
  block "uncommitted changes to tracked files. Commit them, then run /multi-review so the review covers exactly what will be pushed."
fi

state="$top/.claude/review-state/$sha.json"
[ -f "$state" ] || block "no review found for HEAD $sha. Run /multi-review first."

jq -e 'type == "object"' "$state" >/dev/null 2>&1 || block "review state $state is malformed. Re-run /multi-review."

bad_angles=$(jq -r --argjson req "$REQUIRED_ANGLES" '
  $req[] as $a | select((.angles // {})[$a] != "ok") | "  - angle \($a): \((.angles // {})[$a] // "missing")"
' "$state" 2>/dev/null) || block "review state $state is malformed. Re-run /multi-review."

open=$(jq -r '
  ((.waivers // []) | map(.id)) as $waived
  | (.findings // [])[]
  | select(.severity == "critical" or .severity == "high")
  | select(.id as $id | ($waived | index($id)) | not)
  | "  - [\(.severity)] \(.id): \(.title) (\(.file):\(.line // "-"))"
' "$state" 2>/dev/null) || block "review state $state is malformed. Re-run /multi-review."

if [ -n "$bad_angles" ] || [ -n "$open" ]; then
  msg="review for HEAD $sha did not pass."
  [ -n "$bad_angles" ] && msg="$msg\nAngles not reviewed:\n$bad_angles"
  [ -n "$open" ] && msg="$msg\nOpen critical/high findings:\n$open"
  msg="$msg\nFix, commit, and re-run /multi-review. Only the user may waive a finding."
  block "$msg"
fi

exit 0
