#!/usr/bin/env bash
# PreToolUse hook: block `gh pr create` unless the current HEAD has a passing /multi-review.
# Exit 0 = allow, exit 2 = block (stderr is shown to Claude). Fails closed; no bypass flag.
# A workflow guardrail for Claude Code sessions, not a security boundary (the state file is local).
set -uo pipefail

REQUIRED_ANGLES='["functionality","architecture","security","scalability","tests"]'

block() {
  printf 'PR creation blocked by require-review hook: %b\n' "$1" >&2
  exit 2
}

input=$(cat)

# cheap pre-filter so a missing jq only affects commands that could invoke gh
# (no left boundary: in raw JSON a preceding newline is the two chars `\n`)
[[ "$input" =~ gh([^[:alnum:]_-]|$) ]] || exit 0

command -v jq >/dev/null 2>&1 || block "jq is not installed."
command -v git >/dev/null 2>&1 || block "git is not installed."

cmd=$(printf '%s' "$input" | jq -er '.tool_input.command // ""' 2>/dev/null) \
  || block "could not parse hook input."

# Split the command into simple-command segments: drop heredoc bodies, then break on
# ; & | && || newline $( ` ( ) > < . Only a segment whose first word is gh counts, so text that
# merely mentions `gh pr create` (commit messages, grep, echo) passes through.
segments() {
  printf '%s\n' "$1" | awk '
    delim != "" { t = $0; gsub(/^[ \t]+|[ \t]+$/, "", t); if (t == delim) delim = ""; next }
    { print }
    match($0, /<<-?[ \t]*["\047]?[A-Za-z_][A-Za-z0-9_]*/) {
      d = substr($0, RSTART, RLENGTH); gsub(/^<<-?[ \t]*["\047]?/, "", d); delim = d
    }' | sed -E 's/(&&|\|\||\$\(|[;&|`()<>])/\n/g'
}

# gh [global flags [value]] ... ; quotes/backslashes are stripped before matching
flags='([[:space:]]+-[^[:space:]]+([[:space:]]+[^-[:space:]][^[:space:]]*)?)*'
gh_head="^([^[:space:]]*/)?gh${flags}[[:space:]]+"
pr_create_re="${gh_head}pr[[:space:]]+(create|new)([[:space:]]|\$)"
api_re="${gh_head}api[[:space:]]"
pulls_re='/pulls([^[:alnum:]_-]|$)'
api_write_re='(-X|--method)[[:space:]=]*POST|[[:space:]](-f|-F|--field|--raw-field|--input)'
wrapper_re='^(([A-Za-z_][A-Za-z0-9_]*=[^[:space:]]*)|command|sudo|env|exec|time|nohup|(ba|z)?sh[[:space:]]+-l?c)[[:space:]]+'

is_pr_create=false
while IFS= read -r seg; do
  seg=$(printf '%s' "$seg" | tr -d "\"'\\\\")
  seg="${seg#"${seg%%[![:space:]]*}"}"
  while [[ "$seg" =~ $wrapper_re ]]; do seg="${seg:${#BASH_REMATCH[0]}}"; done
  if [[ "$seg" =~ $pr_create_re ]] \
     || { [[ "$seg" =~ $api_re ]] && [[ "$seg" =~ $pulls_re ]] && [[ "$seg" =~ $api_write_re ]]; }; then
    is_pr_create=true
    break
  fi
done < <(segments "$cmd")
$is_pr_create || exit 0

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
