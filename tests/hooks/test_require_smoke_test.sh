#!/usr/bin/env bash
# Tests for .claude/hooks/require-smoke-test.sh, .claude/hooks/smoke_routes.py and
# .claude/skills/smoke-test/smoke.sh
# Run: bash tests/hooks/test_require_smoke_test.sh
set -u

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TMP=$(mktemp -d)  # left for the OS to clean; no rm -rf per repo rules
PASSED=0
FAILED=0

# Stub server standing in for the fixture app below (the fixture is only parsed, never imported)
cat > "$TMP/stub_server.py" <<'PY'
import json, re, sys
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
        path = self.path.split("?")[0]
        if path == "/api/healthcheck":
            self._send(200, {"success": True})
        elif path == "/api/items":
            self._send(200, {"items": [], "auth": bool(self.headers.get("Authorization"))})
        elif re.fullmatch(r"/api/reports/[^/]+", path):
            self._send(200, {"ticker": path.rsplit("/", 1)[1]})
        else:
            self._send(404, {"detail": "Not Found"})
    def do_DELETE(self):
        self._send(404 if re.fullmatch(r"/api/items/[^/]+", self.path) else 405, {"detail": "x"})
    def log_message(self, *a):
        pass

HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
PY

free_port() { python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])'; }

# new_repo <name>: a small FastAPI-shaped app on main, then a feature branch, smoke tooling copied in.
#   GET /api/healthcheck, GET /api/reports/{ticker} -> services.reports.build_report   (main.py)
#   GET /api/items -> ItemService.list_items, DELETE /api/items/{item_id} -> ItemService.delete
#   tasks/crawl.py: crawl() only reached through .delay() from build_report
new_repo() {
  local repo="$TMP/$1"
  mkdir -p "$repo/api" "$repo/services" "$repo/tasks" "$repo/.claude/hooks" "$repo/.claude/skills/smoke-test"
  cat > "$repo/main.py" <<'PY'
from fastapi import FastAPI

from api.items import router as items_router
from services.reports import build_report

app = FastAPI()
app.include_router(items_router)


@app.get("/api/healthcheck")
def healthcheck():
    return {"success": True}


@app.get("/api/reports/{ticker}")
def report(ticker: str):
    return build_report(ticker)
PY
  cat > "$repo/api/items.py" <<'PY'
from fastapi import APIRouter, Depends

from services.items import ItemService

router = APIRouter(prefix="/api/items")


def get_service():
    return ItemService()


@router.get("")
def list_items(service=Depends(get_service)):
    return service.list_items()


@router.delete("/{item_id}")
def delete_item(item_id: str, service=Depends(get_service)):
    return service.delete(item_id)
PY
  cat > "$repo/services/items.py" <<'PY'
class ItemService:
    def list_items(self):
        return []

    def delete(self, item_id):
        return None
PY
  cat > "$repo/services/reports.py" <<'PY'
from tasks.crawl import crawl


def build_report(ticker):
    crawl.delay(ticker)
    return {"ticker": ticker}
PY
  cat > "$repo/tasks/crawl.py" <<'PY'
def crawl(ticker):
    return ticker
PY
  printf '.claude/\n' > "$repo/.gitignore"
  git -C "$repo" init -q -b main
  git -C "$repo" add -A
  git -C "$repo" -c user.email=t@t -c user.name=t commit -qm init
  git -C "$repo" checkout -qb feature
  cp "$ROOT/.claude/hooks/require-smoke-test.sh" "$ROOT/.claude/hooks/smoke-lib.sh" \
     "$ROOT/.claude/hooks/smoke_routes.py" "$repo/.claude/hooks/"
  cp "$ROOT/.claude/skills/smoke-test/smoke.sh" "$repo/.claude/skills/smoke-test/"
  echo "$repo"
}

# edit <repo> <file> <old> <new>: literal one-line replacement
edit() { python3 -c 'import sys; p,o,n=sys.argv[1:]; s=open(p).read(); assert o in s, o; open(p,"w").write(s.replace(o,n,1))' "$1/$2" "$3" "$4"; }

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

hook_reason() {
  jq -n --arg cwd "$1" '{hook_event_name: "Stop", cwd: $cwd}' | bash "$1/.claude/hooks/require-smoke-test.sh" 2>/dev/null \
    | jq -r '.reason // .systemMessage // ""'
}

# affected <repo> -> "METHOD path" lines the analysis requires, sorted
affected() {
  (cd "$1" && . .claude/hooks/smoke-lib.sh && smoke_routes "$1" "$(smoke_changed_files "$1")") \
    | jq -r '.routes[] | "\(.method) \(.path)"' | sort | paste -sd, -
}

# smoke <repo> <args...>: run smoke.sh inside the repo against the stub server -> exit code
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

# --- when is a smoke test required at all ---

mkdir -p "$TMP/norepo/.claude/hooks"
cp "$ROOT/.claude/hooks/require-smoke-test.sh" "$ROOT/.claude/hooks/smoke-lib.sh" "$ROOT/.claude/hooks/smoke_routes.py" "$TMP/norepo/.claude/hooks/"
expect "outside git repo allows" allow "$(run_hook "$TMP/norepo")"

R=$(new_repo none)
expect "no changes allows" allow "$(run_hook "$R")"

R=$(new_repo nonruntime)
mkdir -p "$R/tests" "$R/scripts" "$R/docs"
echo 'def test_x(): pass' > "$R/tests/test_x.py"
echo 'print(1)' > "$R/scripts/tool.py"
echo '# notes' > "$R/docs/notes.md"
expect "tests/scripts/docs changes allow" allow "$(run_hook "$R")"

R=$(new_repo comment)
edit "$R" services/items.py "        return []" "        # empty for now
        return []"
expect "comment-only change allows" allow "$(run_hook "$R")"

R=$(new_repo importonly)
edit "$R" services/items.py "class ItemService:" "import os

class ItemService:"
expect "import-only change allows" allow "$(run_hook "$R")"

R=$(new_repo celery)
edit "$R" tasks/crawl.py "    return ticker" "    return ticker.upper()"
expect "celery-only change reaches no route" "" "$(affected "$R")"
expect "celery-only change allows" allow "$(run_hook "$R")"

R=$(new_repo unreached)
printf '\n\ndef unused():\n    return 1\n' >> "$R/services/items.py"
expect "unreached new function allows" allow "$(run_hook "$R")"

# --- which endpoints are required (relevance) ---

R=$(new_repo method)
edit "$R" services/items.py "        return []" "        return [1]"
expect "method change -> only its route" "GET /api/items" "$(affected "$R")"
expect "method change blocks" block "$(run_hook "$R")"
expect "reason names the route" yes "$(hook_reason "$R" | grep -qF '[ ] GET /api/items' && echo yes)"

R=$(new_repo delete)
edit "$R" services/items.py "        return None" "        return item_id"
expect "other method -> only its route" "DELETE /api/items/{item_id}" "$(affected "$R")"

R=$(new_repo service)
edit "$R" services/reports.py '    return {"ticker": ticker}' '    return {"ticker": ticker.upper()}'
expect "function change -> only its route" "GET /api/reports/{ticker}" "$(affected "$R")"

R=$(new_repo dep)
edit "$R" api/items.py "    return ItemService()" "    return ItemService()  # noqa
    pass"
expect "Depends provider change -> its routes" "DELETE /api/items/{item_id},GET /api/items" "$(affected "$R")"

R=$(new_repo toplevel)
printf '\napp.title = "x"\n' >> "$R/main.py"
expect "module-level change -> that module's routes" "GET /api/healthcheck,GET /api/reports/{ticker}" "$(affected "$R")"

R=$(new_repo newfile)
cat > "$R/api/extra.py" <<'PY'
from fastapi import APIRouter

router = APIRouter()


@router.get("/api/extra")
def extra():
    return {}
PY
expect "untracked router -> its route" "GET /api/extra" "$(affected "$R")"

R=$(new_repo committed)
edit "$R" services/items.py "        return []" "        return [2]"
git -C "$R" -c user.email=t@t -c user.name=t commit -qam change
expect "committed branch change blocks" block "$(run_hook "$R")"

# --- coverage: only checks on affected endpoints count ---

R=$(new_repo cover)
edit "$R" services/reports.py '    return {"ticker": ticker}' '    return {"ticker": ticker.lower()}'
expect "start succeeds" 0 "$(smoke "$R" start)"
expect "unrelated check passes" 0 "$(smoke "$R" check GET /api/healthcheck --contains success)"
expect "unrelated check does not satisfy" block "$(run_hook "$R")"
expect "wrong-method check does not satisfy" 0 "$(smoke "$R" check DELETE /api/items/1 --expect 404)"
expect "still blocked" block "$(run_hook "$R")"
expect "check matches path template" 0 "$(smoke "$R" check GET '/api/reports/AAPL?x=1' --contains AAPL)"
expect "covered route allows" allow "$(run_hook "$R")"
expect "evidence stores affected routes" true "$(jq -r '.affected.ok' "$R/.claude/smoke-state/latest.json")"
git -C "$R" -c user.email=t@t -c user.name=t commit -qam change
expect "commit after passing run still allows" allow "$(run_hook "$R")"
edit "$R" services/reports.py "ticker.lower()" "ticker.title()"
expect "edit after passing run blocks" block "$(run_hook "$R")"
expect "check against stale server refused" 1 "$(smoke "$R" check GET /api/reports/AAPL)"
smoke "$R" stop >/dev/null

R=$(new_repo cap)
printf '\napp.title = "x"\n' >> "$R/main.py"
smoke "$R" start >/dev/null
smoke "$R" check GET /api/healthcheck >/dev/null
expect "1 of 2 routes covered blocks" block "$(run_hook "$R")"
expect "SMOKE_MAX_REQUIRED_ROUTES caps requirement" allow "$(SMOKE_MAX_REQUIRED_ROUTES=1 run_hook "$R")"
smoke "$R" check GET /api/reports/X >/dev/null
expect "all routes covered allows" allow "$(run_hook "$R")"
smoke "$R" stop >/dev/null

# --- failing checks, server lifecycle, options ---

R=$(new_repo failing)
edit "$R" services/items.py "        return []" "        return [3]"
smoke "$R" start >/dev/null
smoke "$R" check GET /api/items >/dev/null
expect "failing check exits 1" 1 "$(smoke "$R" check GET /api/items --contains nope)"
expect "failing check blocks despite coverage" block "$(run_hook "$R")"
expect "reason lists failing check" yes "$(hook_reason "$R" | grep -qF 'GET /api/items: got 200' && echo yes)"
expect "options after positionals" 0 "$(smoke "$R" check GET /api/items --expect 200 --name again)"
smoke "$R" stop >/dev/null

R=$(new_repo noserver)
edit "$R" services/items.py "        return []" "        return [4]"
expect "check without server refused" 1 "$(smoke "$R" check GET /api/items)"
code=$( (cd "$R" && SMOKE_PORT="$PORT" SMOKE_SERVER_CMD="python3 -c 'raise SystemExit(3)'" \
          bash .claude/skills/smoke-test/smoke.sh start) >/dev/null 2>&1; echo $?)
expect "crashing server fails start" 1 "$code"

# --- waivers, subagents, broken state or analysis ---

R=$(new_repo waiver)
edit "$R" services/items.py "        return []" "        return [5]"
expect "short waiver rejected" 1 "$(smoke "$R" skip nope)"
expect "short waiver still blocks" block "$(run_hook "$R")"
expect "waiver recorded" 0 "$(smoke "$R" skip "third-party API key not configured in this environment")"
expect "waiver allows" allow "$(run_hook "$R")"
expect "waiver surfaced to user" yes "$(hook_reason "$R" | grep -q 'third-party API key' && echo yes)"

R=$(new_repo subagent)
edit "$R" services/items.py "        return []" "        return [6]"
expect "reviewer subagent allowed" allow "$(run_hook "$R" SubagentStop backend-review-security)"
expect "Explore subagent allowed" allow "$(run_hook "$R" SubagentStop Explore)"
expect "general-purpose subagent blocked" block "$(run_hook "$R" SubagentStop general-purpose)"
expect "subagent without type blocked" block "$(run_hook "$R" SubagentStop)"

R=$(new_repo malformed)
edit "$R" services/items.py "        return []" "        return [7]"
fp=$(cd "$R" && bash .claude/skills/smoke-test/smoke.sh status | sed -n 's/^fingerprint: //p')
mkdir -p "$R/.claude/smoke-state"
echo 'not json' > "$R/.claude/smoke-state/$fp.json"
expect "malformed state blocks" block "$(run_hook "$R")"
echo '{"checks": [], "waiver": null}' > "$R/.claude/smoke-state/$fp.json"
expect "empty checks block" block "$(run_hook "$R")"

R=$(new_repo broken)
echo 'def broken(:' > "$R/services/broken.py"
expect "unparseable code blocks" block "$(run_hook "$R")"
expect "reason says analysis failed" yes "$(hook_reason "$R" | grep -q 'Route analysis failed' && echo yes)"
smoke "$R" start >/dev/null
smoke "$R" check GET /api/healthcheck >/dev/null
smoke "$R" stop >/dev/null
expect "analysis failure falls back to any passing check" allow "$(run_hook "$R")"

echo
echo "passed: $PASSED, failed: $FAILED"
[ "$FAILED" -eq 0 ]
