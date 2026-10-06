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

case "$verdict" in
  pass) exit 0 ;;
  waived)
    reason=$(jq -r '.waiver.reason' "$state")
    jq -n --arg m "Smoke test waived for $fp: $reason" '{systemMessage: $m}'
    exit 0 ;;
esac

n=$(printf '%s\n' "$changed" | wc -l | tr -d ' ')
listing=$(printf '%s\n' "$changed" | head -15 | sed 's/^/  - /')
[ "$n" -gt 15 ] && listing="$listing"$'\n'"  ... and $((n - 15)) more"

case "$verdict" in
  missing|empty) why="No smoke results are recorded for the current code (fingerprint $fp)." ;;
  failing)
    failed=$(jq -r '.checks[] | select(.pass != true) | "  - \(.method) \(.path): got \(.http_status) (expected \(.expect))"' "$state")
    why="Smoke checks are failing for the current code (fingerprint $fp):"$'\n'"$failed" ;;
  *) why="Smoke state $state is malformed. Re-run the smoke checks." ;;
esac

S=.claude/skills/smoke-test/smoke.sh
block "Smoke test required before finishing. You changed runtime code that the API serves:
$listing

$why

Follow the smoke-test skill (.claude/skills/smoke-test/SKILL.md):
  1. $S status          # affected routes + what is recorded so far
  2. $S start           # boots the dev server from the current code
  3. $S check GET /api/... --expect 200 [--auth] [--data '{...}'] [--contains TEXT]
     (one check per affected endpoint: happy path, plus an error path where it matters)
  4. $S stop
Any later edit to runtime code changes the fingerprint and needs a fresh run.
Fix the code if a check fails; do not weaken the expectation to make it pass.
Only if the change genuinely cannot be exercised over HTTP here (e.g. missing credentials for a
third-party API), record why with: $S skip \"<reason>\" — and tell the user in your reply."
