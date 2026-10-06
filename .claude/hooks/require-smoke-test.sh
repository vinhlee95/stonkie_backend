#!/usr/bin/env bash
# Stop / SubagentStop hook: an agent that changed runtime code (api/, services/, connectors/,
# main.py, ...) may not finish until the current code has a passing smoke run recorded by
# .claude/skills/smoke-test/smoke.sh, or an explicit waiver with a reason.
# Allow = exit 0 with no output. Block = {"decision":"block","reason":...} on stdout, which
# Claude Code feeds back to the agent so it keeps working.
# A workflow guardrail for Claude Code sessions, not a security boundary (the state file is local).
set -uo pipefail

# Read-only agents never change code, so they are never asked to smoke test.
READ_ONLY_AGENTS='^(backend-review-.*|Explore|Plan|claude-code-guide|statusline-setup)$'

block() {
  jq -n --arg r "$1" '{decision: "block", reason: $r}'
  exit 0
}

input=$(cat)

if ! command -v jq >/dev/null 2>&1; then
  echo "require-smoke-test hook: jq is not installed; install jq so the smoke-test gate can run." >&2
  exit 2
fi

event=$(printf '%s' "$input" | jq -r '.hook_event_name // "Stop"' 2>/dev/null)
if [ "$event" = "SubagentStop" ]; then
  agent_type=$(printf '%s' "$input" | jq -r '.agent_type // ""' 2>/dev/null)
  [[ "$agent_type" =~ $READ_ONLY_AGENTS ]] && exit 0
fi

cwd=$(printf '%s' "$input" | jq -r '.cwd // ""' 2>/dev/null)
[ -n "$cwd" ] && [ -d "$cwd" ] || cwd=$PWD
top=$(git -C "$cwd" rev-parse --show-toplevel 2>/dev/null) || exit 0

# shellcheck source=smoke-lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/smoke-lib.sh"

changed=$(smoke_changed_files "$top")
[ -n "$changed" ] || exit 0

fp=$(smoke_fingerprint "$top" "$changed")
state="$(smoke_state_dir "$top")/$fp.json"
verdict=$(smoke_verdict "$state")

if [ "$verdict" = waived ]; then
  reason=$(jq -r '.waiver.reason' "$state")
  jq -n --arg m "Smoke test waived for $fp: $reason" '{systemMessage: $m}'
  exit 0
fi

# Which endpoints reach the changed code? Fall back to "any passing check" if the analysis fails.
analysis_err=$(mktemp)
analysis=$(smoke_routes "$top" "$changed" "$state" 2>"$analysis_err") || analysis=""
analysis_error=$(head -c 400 "$analysis_err"); rm -f "$analysis_err"
if [ -n "$analysis" ]; then
  n_routes=$(printf '%s' "$analysis" | jq '.routes | length')
  [ "$n_routes" -eq 0 ] && exit 0  # the change reaches no HTTP route: nothing to curl
  covered_ok=$(printf '%s' "$analysis" | jq -r '.ok')
  [ "$verdict" = pass ] && [ "$covered_ok" = true ] && exit 0
else
  [ "$verdict" = pass ] && exit 0
fi

S=.claude/skills/smoke-test/smoke.sh
case "$verdict" in
  failing)
    failed=$(jq -r '.checks[] | select(.pass != true) | "  - \(.method) \(.path): got \(.http_status) (expected \(.expect))"' "$state")
    why="Smoke checks are failing for the current code (fingerprint $fp):"$'\n'"$failed"$'\n'"Fix the code; do not weaken an expectation to make it pass. Every recorded check must pass." ;;
  malformed) why="Smoke state $state is malformed. Re-run the smoke checks." ;;
  *) why="" ;;
esac

if [ -n "$analysis" ]; then
  routes=$(printf '%s' "$analysis" | jq -r --arg S "$S" '
    "These endpoints reach the code you changed. Each needs a passing check (need \(.required), have \(.covered)):",
    (.routes[] | "  [\(if .covered then "x" else " " end)] \(.method) \(.path)   (\(.via[0]) -> ... -> \(.via[-1]))")')
else
  routes="Route analysis failed ($analysis_error), so any passing check counts. Check the endpoints that call the changed code."
fi

block "Smoke test required before finishing (fingerprint $fp).
${why:+$why

}$routes

Follow the smoke-test skill (.claude/skills/smoke-test/SKILL.md):
  1. $S status     # affected endpoints, why each is affected, what is covered
  2. $S start      # boots the dev server from the current code
  3. $S check METHOD /real/path [--expect CODE] [--auth] [-d '{...}'] [--contains TEXT]
     (fill {params} with real values; happy path, plus an error path you touched)
  4. $S stop
Any later edit to runtime code changes the fingerprint and needs a fresh run.
Only if an endpoint genuinely cannot be exercised over HTTP here (e.g. missing credentials for a
third-party API), record why with: $S skip \"<reason>\", and tell the user in your reply."
