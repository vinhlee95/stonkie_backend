#!/usr/bin/env bash
# Tests for .claude/hooks/require-smoke-test.sh and .claude/skills/smoke-test/smoke.sh
# Run: bash tests/hooks/test_require_smoke_test.sh
set -u

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TMP=$(mktemp -d)  # left for the OS to clean; no rm -rf per repo rules
PASSED=0
FAILED=0

# Stub API: /api/healthcheck -> 200, /api/items -> 200 [] (echoes Authorization presence), else 404
cat > "$TMP/stub_server.py" <<'PY'
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer

class H(BaseHTTPRequestHandler):
    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
    def do_GET(self):
        if self.path == "/api/healthcheck":
            self._send(200, {"success": True})
        elif self.path == "/api/items":
            self._send(200, {"items": [], "auth": bool(self.headers.get("Authorization"))})
        else:
            self._send(404, {"detail": "Not Found"})
    do_POST = do_GET
    def log_message(self, *a):
        pass

HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
PY

free_port() { python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])'; }

# new_repo <name>: main branch with one runtime file, then a feature branch, smoke tooling copied in
new_repo() {
  local repo="$TMP/$1"
  mkdir -p "$repo/api" "$repo/.claude/hooks" "$repo/.claude/skills/smoke-test"
  git -C "$repo" init -q -b main
  echo 'x = 1' > "$repo/api/items.py"
  printf '.claude/\n' > "$repo/.gitignore"
  git -C "$repo" add -A
  git -C "$repo" -c user.email=t@t -c user.name=t commit -qm init
  git -C "$repo" checkout -qb feature
  cp "$ROOT/.claude/hooks/require-smoke-test.sh" "$ROOT/.claude/hooks/smoke-lib.sh" "$repo/.claude/hooks/"
  cp "$ROOT/.claude/skills/smoke-test/smoke.sh" "$repo/.claude/skills/smoke-test/"
  echo "$repo"
}

# run_hook <cwd> [event] [agent_type] -> prints "allow" or "block" (or "error:<code>")
run_hook() {
  local out code
  out=$(jq -n --arg cwd "$1" --arg e "${2:-Stop}" --arg a "${3:-}" \
          '{hook_event_name: $e, cwd: $cwd} + (if $a == "" then {} else {agent_type: $a} end)' \
        | bash "$1/.claude/hooks/require-smoke-test.sh" 2>/dev/null)
  code=$?
  [ "$code" -eq 0 ] || { echo "error:$code"; return; }
  if printf '%s' "$out" | jq -e '.decision == "block"' >/dev/null 2>&1; then echo block; else echo allow; fi
}

hook_output() {
  jq -n --arg cwd "$1" '{hook_event_name: "Stop", cwd: $cwd}' | bash "$1/.claude/hooks/require-smoke-test.sh" 2>/dev/null
}

# smoke <repo> <args...>: run smoke.sh inside the repo against the stub server
smoke() {
  local repo=$1; shift
  (cd "$repo" && SMOKE_PORT="$PORT" SMOKE_START_TIMEOUT=15 \
     SMOKE_SERVER_CMD="python3 $TMP/stub_server.py $PORT" bash .claude/skills/smoke-test/smoke.sh "$@") >/dev/null 2>&1
  echo $?
}

expect() {  # expect <name> <expected> <actual>
  if [ "$2" = "$3" ]; then PASSED=$((PASSED + 1)); echo "ok   - $1"
  else FAILED=$((FAILED + 1)); echo "FAIL - $1 (expected $2, got $3)"; fi
}

PORT=$(free_port)

# 1. outside a git repo -> allow
mkdir -p "$TMP/norepo/.claude/hooks"
cp "$ROOT/.claude/hooks/require-smoke-test.sh" "$ROOT/.claude/hooks/smoke-lib.sh" "$TMP/norepo/.claude/hooks/"
expect "outside git repo allows" allow "$(run_hook "$TMP/norepo")"

# 2. no changes -> allow
R=$(new_repo r2)
expect "no changes allows" allow "$(run_hook "$R")"

# 3. non-runtime changes only (tests, scripts, docs, alembic) -> allow
R=$(new_repo r3)
mkdir -p "$R/tests" "$R/scripts" "$R/alembic" "$R/docs"
echo 'def test_x(): pass' > "$R/tests/test_x.py"
echo 'print(1)' > "$R/scripts/tool.py"
echo 'rev = 1' > "$R/alembic/v1.py"
echo '# notes' > "$R/docs/notes.md"
echo 'def test_y(): pass' > "$R/test_root.py"
expect "non-runtime changes allow" allow "$(run_hook "$R")"

# 4. unstaged runtime change, no results -> block
R=$(new_repo r4)
echo 'x = 2' > "$R/api/items.py"
expect "unstaged runtime change blocks" block "$(run_hook "$R")"
expect "block reason names the file" yes "$(hook_output "$R" | jq -r '.reason' | grep -q 'api/items.py' && echo yes)"

# 5. untracked runtime file -> block
R=$(new_repo r5)
mkdir -p "$R/services" && echo 'y = 1' > "$R/services/new.py"
expect "untracked runtime file blocks" block "$(run_hook "$R")"

# 6. committed runtime change on the branch (vs merge-base with main) -> block
R=$(new_repo r6)
echo 'x = 3' > "$R/api/items.py"
git -C "$R" -c user.email=t@t -c user.name=t commit -qam change
expect "committed branch change blocks" block "$(run_hook "$R")"

# 7. passing smoke run -> allow; committing afterwards keeps it valid (content fingerprint)
R=$(new_repo r7)
echo 'x = 4' > "$R/api/items.py"
expect "start succeeds" 0 "$(smoke "$R" start)"
expect "passing check exits 0" 0 "$(smoke "$R" check GET /api/healthcheck --contains success)"
expect "options after positionals" 0 "$(smoke "$R" check --name auth-less GET /api/items --contains items)"
smoke "$R" stop >/dev/null
expect "passing run allows" allow "$(run_hook "$R")"
git -C "$R" -c user.email=t@t -c user.name=t commit -qam change
expect "commit after passing run still allows" allow "$(run_hook "$R")"
expect "latest.json written" pass "$(jq -r 'if all(.checks[]; .pass) then "pass" else "fail" end' "$R/.claude/smoke-state/latest.json")"
expect "evidence records actual status" 200 "$(jq -r '.checks[0].http_status' "$R/.claude/smoke-state/latest.json")"

# 8. editing runtime code after a passing run -> block again
echo 'x = 5' > "$R/api/items.py"
expect "edit after passing run blocks" block "$(run_hook "$R")"

# 9. a failing check -> block, reason lists it
R=$(new_repo r9)
echo 'x = 6' > "$R/api/items.py"
smoke "$R" start >/dev/null
smoke "$R" check GET /api/healthcheck >/dev/null
expect "failing check exits 1" 1 "$(smoke "$R" check GET /api/missing)"
smoke "$R" stop >/dev/null
expect "failing check blocks" block "$(run_hook "$R")"
expect "reason lists failing check" yes "$(hook_output "$R" | jq -r '.reason' | grep -q 'GET /api/missing: got 404' && echo yes)"

# 10. --expect matching a non-2xx status passes
R=$(new_repo r10)
echo 'x = 7' > "$R/api/items.py"
smoke "$R" start >/dev/null
expect "expected 404 passes" 0 "$(smoke "$R" check GET /api/missing --expect 404)"
expect "--contains mismatch fails" 1 "$(smoke "$R" check GET /api/healthcheck --contains nope)"

# 11. stale server: runtime edit after start -> check refuses and records nothing
echo 'x = 8' > "$R/api/items.py"
expect "check against stale server refused" 1 "$(smoke "$R" check GET /api/healthcheck)"
smoke "$R" stop >/dev/null
expect "stale check recorded nothing" block "$(run_hook "$R")"

# 12. check without a running server refused
R=$(new_repo r12)
echo 'x = 9' > "$R/api/items.py"
expect "check without server refused" 1 "$(smoke "$R" check GET /api/healthcheck)"

# 13. server that dies on startup -> start fails
R=$(new_repo r13)
echo 'x = 10' > "$R/api/items.py"
code=$( (cd "$R" && SMOKE_PORT="$PORT" SMOKE_SERVER_CMD="python3 -c 'raise SystemExit(3)'" \
          bash .claude/skills/smoke-test/smoke.sh start) >/dev/null 2>&1; echo $?)
expect "crashing server fails start" 1 "$code"

# 14. waiver: too-short reason rejected; real reason allows with a systemMessage
R=$(new_repo r14)
echo 'x = 11' > "$R/api/items.py"
expect "short waiver rejected" 1 "$(smoke "$R" skip nope)"
expect "short waiver still blocks" block "$(run_hook "$R")"
expect "waiver recorded" 0 "$(smoke "$R" skip "third-party API key not configured in this environment")"
expect "waiver allows" allow "$(run_hook "$R")"
expect "waiver surfaced to user" yes "$(hook_output "$R" | jq -r '.systemMessage' | grep -q 'third-party API key' && echo yes)"

# 15. SubagentStop: read-only agents pass, code-writing agents are gated
R=$(new_repo r15)
echo 'x = 12' > "$R/api/items.py"
expect "reviewer subagent allowed" allow "$(run_hook "$R" SubagentStop backend-review-security)"
expect "Explore subagent allowed" allow "$(run_hook "$R" SubagentStop Explore)"
expect "general-purpose subagent blocked" block "$(run_hook "$R" SubagentStop general-purpose)"
expect "subagent without type blocked" block "$(run_hook "$R" SubagentStop)"

# 16. malformed or hand-written empty state -> block
R=$(new_repo r16)
echo 'x = 13' > "$R/api/items.py"
smoke "$R" status >/dev/null
fp=$(cd "$R" && bash .claude/skills/smoke-test/smoke.sh status | sed -n 's/^fingerprint: //p')
mkdir -p "$R/.claude/smoke-state"
echo 'not json' > "$R/.claude/smoke-state/$fp.json"
expect "malformed state blocks" block "$(run_hook "$R")"
echo '{"checks": [], "waiver": null}' > "$R/.claude/smoke-state/$fp.json"
expect "empty checks block" block "$(run_hook "$R")"

echo
echo "passed: $PASSED, failed: $FAILED"
[ "$FAILED" -eq 0 ]
