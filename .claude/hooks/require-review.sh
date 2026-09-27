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
# (no left boundary: in raw JSON a preceding newline is the two chars `\n`; `\u` escapes can spell gh)
[[ "$input" =~ gh([^[:alnum:]_-]|$) || "$input" == *'\u'* ]] || exit 0

command -v jq >/dev/null 2>&1 || block "jq is not installed."
command -v git >/dev/null 2>&1 || block "git is not installed."

cmd=$(printf '%s' "$input" | jq -er '.tool_input.command // ""' 2>/dev/null) \
  || block "could not parse hook input."

# Split the command into simple-command segments, one per line, the way a shell would:
# - backslash-newline is removed first (line continuation, also inside double quotes)
# - break on ; & | && || newline > < outside quotes
# - $( ` ( open a nested command (also inside double quotes) and ) ` close it again,
#   restoring the surrounding quote state
# - single-quoted text is data; a here-string word is data but its $( ) still runs
# - heredoc bodies are dropped, unless the heredoc is never terminated
# Only a segment whose first word is gh counts, so text that merely mentions
# `gh pr create` (commit messages, grep, echo) passes through.
segments() {
  local joined=${1//$'\\\n'/}
  printf '%s\n' "$joined" | awk '
    function flush() { print buf; buf = "" }
    function push(o) { flush(); sp++; sq[sp] = q; so[sp] = o; q = "" }
    function pop() { flush(); q = sq[sp]; sp-- }
    delim != "" {
      t = $0; gsub(/^[ \t]+|[ \t]+$/, "", t)
      if (t == delim) { delim = ""; nb = 0 } else body[++nb] = $0
      next
    }
    {
      line = $0; n = length(line); i = 1
      while (i <= n) {
        c = substr(line, i, 1); c2 = substr(line, i, 2)
        if (q == "\047") { if (c == "\047") q = ""; buf = buf (c == "\047" ? c : (c ~ /[;&|`()<>$]/ ? " " : c)); i++; continue }
        if (q == "\"") {
          if (c == "\\") { buf = buf " "; i += 2; continue }
          if (c == "\"") { q = ""; buf = buf c; i++; continue }
          if (c2 == "$(") { push("("); i += 2; continue }
          if (c == "`") { push("`"); i++; continue }
          buf = buf (c ~ /[;&|()<>]/ ? " " : c); i++; continue
        }
        if (c == "`") { if (sp > 0 && so[sp] == "`") pop(); else push("`"); i++; continue }
        if (c2 == "$(") { push("("); i += 2; continue }
        if (c == "(") { push("("); i++; continue }
        if (c == ")") { if (sp > 0 && so[sp] == "(") pop(); else flush(); i++; continue }
        if (substr(line, i, 3) == "<<<") { flush(); buf = "HERESTRING "; i += 3; continue }
        if (c2 == "<<" && match(substr(line, i + 2), /^-?[ \t]*["\047]?[A-Za-z_][A-Za-z0-9_]*["\047]?/)) {
          d = substr(line, i + 2 + RSTART - 1, RLENGTH); gsub(/^-?[ \t]*["\047]?|["\047]$/, "", d)
          delim = d; nb = 0; i += 2 + RLENGTH; flush(); continue
        }
        if (c == "\047" || c == "\"") { q = c; buf = buf c; i++; continue }
        if (c == "\\") { buf = buf substr(line, i, 2); i += 2; continue }
        if (c ~ /[;&|<>]/) { flush(); i++; continue }
        buf = buf c; i++
      }
      if (q == "") flush(); else buf = buf " "
    }
    END { if (buf != "") flush(); for (k = 1; k <= nb; k++) print body[k] }'
}

# gh [global flags [value]] ... ; quotes/backslashes are stripped before matching
flags='([[:space:]]+-[^[:space:]]+([[:space:]]+[^-[:space:]][^[:space:]]*)?)*'
gh_head="^([^[:space:]]*/)?gh${flags}[[:space:]]+"
pr_create_re="${gh_head}pr[[:space:]]+(create|new)([[:space:]]|\$)"
api_re="${gh_head}api[[:space:]]"
# only the create endpoint; /pulls/<n>/... (comments, reviews) is not PR creation
pulls_re='/pulls([?[:space:]]|$)'
api_write_re='(-X|--method)[[:space:]=]*POST|[[:space:]](-f|-F|--field|--raw-field|--input)'
# commands/keywords that run the rest of the segment as a command; options/numbers after them are skipped
wrapper_re='^(([A-Za-z_][A-Za-z0-9_]*=[^[:space:]]*)|command|sudo|env|exec|eval|time|nohup|nice|timeout|xargs|if|then|else|elif|do|while|until|!|\{|(ba|z)?sh[[:space:]]+-l?c)[[:space:]]+'
wrapper_arg_re='^(-[^[:space:]]*|[0-9]+[smhd]?)[[:space:]]+'
cd_re='^(cd|pushd)([[:space:]]+([^[:space:]]+))?[[:space:]]*$'

is_pr_create=false
pr_seg=""
cd_target=""
while IFS= read -r seg; do
  seg=$(printf '%s' "$seg" | tr -d "\"'\\\\")
  seg="${seg#"${seg%%[![:space:]]*}"}"
  wrapped=false
  while :; do
    if [[ "$seg" =~ $wrapper_re ]]; then seg="${seg:${#BASH_REMATCH[0]}}"; wrapped=true
    elif $wrapped && [[ "$seg" =~ $wrapper_arg_re ]]; then seg="${seg:${#BASH_REMATCH[0]}}"
    else break; fi
  done
  if [[ "$seg" =~ $cd_re ]]; then cd_target="${BASH_REMATCH[3]:-$HOME}"; continue; fi
  if [[ "$seg" =~ $pr_create_re ]] \
     || { [[ "$seg" =~ $api_re ]] && [[ "$seg" =~ $pulls_re ]] && [[ "$seg" =~ $api_write_re ]]; }; then
    is_pr_create=true
    pr_seg="$seg"
    break
  fi
done < <(segments "$cmd")
$is_pr_create || exit 0

cwd=$(printf '%s' "$input" | jq -r '.cwd // empty')
[ -n "$cwd" ] || cwd="${CLAUDE_PROJECT_DIR:-$PWD}"
# `cd <dir> && gh pr create` creates the PR from <dir>: judge that repo
if [ -n "$cd_target" ]; then
  case "$cd_target" in
    "~"*) cwd="$HOME${cd_target#\~}" ;;
    /*) cwd="$cd_target" ;;
    *) cwd="$cwd/$cd_target" ;;
  esac
fi

top=$(git -C "$cwd" rev-parse --show-toplevel 2>/dev/null) || block "$cwd is not inside a git repository."
sha=$(git -C "$top" rev-parse HEAD 2>/dev/null) || block "could not resolve HEAD in $top."

# the PR must target the reviewed repo and branch
if [[ "$pr_seg" =~ (^|[[:space:]])(-R|--repo)([[:space:]]+|=)([^[:space:]]+) ]]; then
  want=$(printf '%s' "${BASH_REMATCH[4]}" | tr '[:upper:]' '[:lower:]' | awk -F/ '{print $(NF-1) "/" $NF}')
  have=$(git -C "$top" remote get-url origin 2>/dev/null | tr '[:upper:]' '[:lower:]' | sed -E 's#\.git$##; s#^.*[:/]([^/:]+/[^/]+)$#\1#')
  [ -n "$have" ] && [ "$want" = "$have" ] || block "--repo $want is not this repository's origin (${have:-none}). Create the PR from the reviewed repo."
fi
if [[ "$pr_seg" =~ (^|[[:space:]])(-H|--head)([[:space:]]+|=)([^[:space:]]+) ]]; then
  head_branch="${BASH_REMATCH[4]##*:}"
  current=$(git -C "$top" rev-parse --abbrev-ref HEAD 2>/dev/null)
  [ "$head_branch" = "$current" ] || block "--head $head_branch is not the reviewed branch ($current). Check it out and run /multi-review there."
fi

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
