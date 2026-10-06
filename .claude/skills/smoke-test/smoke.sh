#!/usr/bin/env bash
# Smoke-test runner: boots the dev server from the current code, curls endpoints and records
# machine-generated evidence in .claude/smoke-state/<fingerprint>.json (+ latest.json), which
# the require-smoke-test Stop hook and the orchestrating agent read.
#
#   smoke.sh status                      changed runtime files, affected routes, recorded results
#   smoke.sh start                       (re)start the dev server on $SMOKE_PORT and wait for health
#   smoke.sh check [opts] METHOD PATH    curl the server and record the result
#       --expect CODE      expected HTTP status (default 200)
#       --contains TEXT    response body must contain TEXT
#       -d, --data JSON    request body (sent as application/json)
#       -H, --header H     extra request header (repeatable)
#       --auth             send a backend JWT for a smoke-test user (needs BACKEND_JWT_SECRET)
#       --stream           SSE/streaming endpoint: a --max-time cut-off after data arrived is OK
#       --max-time N       curl timeout in seconds (default 60)
#       --name LABEL       label for the check
#   smoke.sh skip REASON                 record a waiver when the change can't be exercised over HTTP
#   smoke.sh stop                        stop the dev server
#
# Env: SMOKE_PORT (default 8765), SMOKE_SERVER_CMD (default hypercorn main:app on that port),
#      SMOKE_HEALTH_PATH (default /api/healthcheck), SMOKE_START_TIMEOUT seconds (default 90).
set -uo pipefail

die() { printf 'smoke: %s\n' "$*" >&2; exit 1; }
command -v jq >/dev/null 2>&1 || die "jq is required"
command -v curl >/dev/null 2>&1 || die "curl is required"

TOP=$(git rev-parse --show-toplevel 2>/dev/null) || die "run inside the repository"
cd "$TOP" || exit 1
# shellcheck source=../../hooks/smoke-lib.sh
. "$TOP/.claude/hooks/smoke-lib.sh"

PORT=${SMOKE_PORT:-8765}
BASE_URL="http://127.0.0.1:$PORT"
HEALTH=${SMOKE_HEALTH_PATH:-/api/healthcheck}
DIR=$(smoke_state_dir "$TOP")
SERVER="$DIR/server.json"
LOG="$DIR/server.log"
mkdir -p "$DIR"

now() { date -u +%Y-%m-%dT%H:%M:%SZ; }
CHANGED=$(smoke_changed_files "$TOP")
FP=$(smoke_fingerprint "$TOP" "$CHANGED")
STATE="$DIR/$FP.json"

python_bin() { if [ -x "$TOP/venv/bin/python" ]; then echo "$TOP/venv/bin/python"; else echo python3; fi; }

server_alive() {
  [ -f "$SERVER" ] || return 1
  local pid; pid=$(jq -r '.pid // empty' "$SERVER" 2>/dev/null)
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

stop_server() {
  [ -f "$SERVER" ] || return 0
  local pid; pid=$(jq -r '.pid // empty' "$SERVER" 2>/dev/null)
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    kill -- "-$pid" 2>/dev/null || kill "$pid" 2>/dev/null
    for _ in $(seq 1 20); do kill -0 "$pid" 2>/dev/null || break; sleep 0.25; done
    kill -9 -- "-$pid" 2>/dev/null || kill -9 "$pid" 2>/dev/null
  fi
  rm -f "$SERVER"
}

ensure_state() {
  [ -f "$STATE" ] && return 0
  jq -n --arg fp "$FP" --arg base "$(smoke_base "$TOP")" --arg head "$(git rev-parse HEAD 2>/dev/null)" \
    --arg at "$(now)" --arg changed "$CHANGED" \
    '{fingerprint: $fp, base: $base, head: $head, created_at: $at, updated_at: $at,
      changed_files: ($changed | split("\n") | map(select(length > 0))), checks: [], waiver: null}' > "$STATE"
}

# update_state <jq-filter> [jq args...]: apply a filter to the state file and refresh latest.json
update_state() {
  local filter=$1; shift
  ensure_state
  jq --arg at "$(now)" "$@" "$filter | .updated_at = \$at" "$STATE" > "$STATE.tmp" && mv "$STATE.tmp" "$STATE"
  cp "$STATE" "$DIR/latest.json"
}

# Route analysis (see .claude/hooks/smoke_routes.py); prints nothing if it fails.
routes_json() { smoke_routes "$TOP" "$CHANGED" "$STATE" 2>/dev/null; }

# coverage_line <routes-json>
coverage_line() {
  jq -r '"coverage: \(.covered)/\(.routes | length) affected endpoints have a passing check (need \(.required))"' <<<"$1"
}

cmd=${1:-status}; shift || true
case "$cmd" in
  status)
    echo "fingerprint: $FP"
    if [ -z "$CHANGED" ]; then echo "no runtime changes vs base: smoke test not required"; exit 0; fi
    echo "changed runtime files:"; printf '  - %s\n' $CHANGED
    analysis=$(routes_json)
    if [ -z "$analysis" ]; then
      echo "route analysis failed (run: python3 .claude/hooks/smoke_routes.py ... to see why); any passing check counts"
    elif [ "$(jq '.routes | length' <<<"$analysis")" -eq 0 ]; then
      echo "changed functions: $(jq -r '.changed_symbols | join(", ")' <<<"$analysis")"
      echo "no HTTP route reaches the changed code: smoke test not required"
    else
      echo "changed functions: $(jq -r '.changed_symbols | join(", ")' <<<"$analysis")"
      echo "endpoints that reach them ([x] = has a passing check):"
      jq -r '.routes[] | "  [\(if .covered then "x" else " " end)] \(.method) \(.path)\n        via \(.via | join(" -> "))"' <<<"$analysis"
      coverage_line "$analysis"
    fi
    echo "verdict: $(smoke_verdict "$STATE")"
    [ -f "$STATE" ] && jq -r '(.checks[] | "  [\(if .pass then "PASS" else "FAIL" end)] \(.method) \(.path) -> \(.http_status) (expect \(.expect))"),
                             (if .waiver then "  waiver: \(.waiver.reason)" else empty end)' "$STATE"
    echo "server: $(server_alive && echo "running on $BASE_URL" || echo stopped)"
    ;;

  start)
    stop_server
    if curl -s -o /dev/null --max-time 2 "$BASE_URL$HEALTH"; then
      die "port $PORT is already in use by another process; set SMOKE_PORT to a free port"
    fi
    server_cmd=${SMOKE_SERVER_CMD:-"$(python_bin) -m hypercorn main:app --bind 127.0.0.1:$PORT"}
    : > "$LOG"
    setsid bash -c "$server_cmd" >>"$LOG" 2>&1 < /dev/null &
    pid=$!
    jq -n --argjson pid "$pid" --arg port "$PORT" --arg fp "$FP" --arg at "$(now)" --arg log "$LOG" --arg cmd "$server_cmd" \
      '{pid: $pid, port: $port, fingerprint: $fp, started_at: $at, log: $log, cmd: $cmd}' > "$SERVER"
    deadline=$(( $(date +%s) + ${SMOKE_START_TIMEOUT:-90} ))
    until curl -sf -o /dev/null --max-time 2 "$BASE_URL$HEALTH"; do
      if ! kill -0 "$pid" 2>/dev/null; then
        tail -40 "$LOG" >&2; rm -f "$SERVER"; die "server exited during startup (log: $LOG)"
      fi
      if [ "$(date +%s)" -ge "$deadline" ]; then
        tail -40 "$LOG" >&2; stop_server; die "server not healthy at $BASE_URL$HEALTH within timeout (log: $LOG)"
      fi
      sleep 1
    done
    echo "server up at $BASE_URL (pid $pid, fingerprint $FP, log $LOG)"
    ;;

  check)
    expect=200 contains="" data="" auth=0 stream=0 max_time=60 name="" headers=() pos=()
    while [ $# -gt 0 ]; do
      case "$1" in
        --expect|--contains|-d|--data|-H|--header|--max-time|--name)
          [ $# -ge 2 ] || die "$1 needs a value" ;;&
        --expect) expect=$2; shift 2 ;;
        --contains) contains=$2; shift 2 ;;
        -d|--data) data=$2; shift 2 ;;
        -H|--header) headers+=("$2"); shift 2 ;;
        --auth) auth=1; shift ;;
        --stream) stream=1; shift ;;
        --max-time) max_time=$2; shift 2 ;;
        --name) name=$2; shift 2 ;;
        -*) die "unknown option $1" ;;
        *) pos+=("$1"); shift ;;
      esac
    done
    [ ${#pos[@]} -eq 2 ] || die "usage: smoke.sh check [opts] METHOD PATH [opts]"
    method=$(tr '[:lower:]' '[:upper:]' <<<"${pos[0]}") path=${pos[1]}
    [[ "$path" == /* ]] || die "PATH must start with / (got $path)"
    [[ "$expect" =~ ^[0-9]{3}$ ]] || die "--expect must be an HTTP status code"
    server_alive || die "server is not running; run: smoke.sh start"
    server_fp=$(jq -r '.fingerprint' "$SERVER")
    [ "$server_fp" = "$FP" ] || die "runtime code changed since the server started ($server_fp -> $FP); run: smoke.sh start"

    args=(-sS -N -X "$method" --max-time "$max_time" -o "$DIR/.body" -w '%{http_code}')
    for h in "${headers[@]+"${headers[@]}"}"; do args+=(-H "$h"); done
    [ -n "$data" ] && args+=(-H 'Content-Type: application/json' --data-raw "$data")
    if [ "$auth" = 1 ]; then
      token=$(cd "$TOP" && "$(python_bin)" - <<'PY'
import os, time
import jwt
from dotenv import load_dotenv
load_dotenv(os.path.join(os.getcwd(), ".env"))
secret = os.environ.get("BACKEND_JWT_SECRET", "")
if len(secret.encode()) < 32:
    raise SystemExit("BACKEND_JWT_SECRET is missing or shorter than 32 bytes")
now = int(time.time())
print(jwt.encode({"sub": os.environ.get("SMOKE_AUTH_SUB", "smoke-test-user"),
                  "email": os.environ.get("SMOKE_AUTH_EMAIL", "smoke-test@stonkie.local"),
                  "name": "Smoke Test", "iss": "stonkie-web", "aud": "stonkie-api",
                  "iat": now, "exp": now + 600}, secret, algorithm="HS256"))
PY
      ) || die "could not mint an auth token"
      args+=(-H "Authorization: Bearer $token")
    fi

    start_ms=$(date +%s%3N)
    : > "$DIR/.body"
    status=$(curl "${args[@]}" "$BASE_URL$path" 2>"$DIR/.curl_err"); curl_exit=$?
    duration=$(( $(date +%s%3N) - start_ms ))
    [[ "$status" =~ ^[0-9]{3}$ ]] || status=0
    bytes=$(wc -c < "$DIR/.body" | tr -d ' ')

    pass=true
    [ "$status" = "$expect" ] || pass=false
    if [ -n "$contains" ] && ! grep -qF -- "$contains" "$DIR/.body"; then pass=false; fi
    if [ "$curl_exit" -ne 0 ] && ! { [ "$stream" = 1 ] && [ "$curl_exit" -eq 28 ] && [ "$bytes" -gt 0 ]; }; then pass=false; fi
    log_tail=""; [ "$pass" = false ] && log_tail=$(tail -30 "$LOG" 2>/dev/null)

    update_state '.checks += [{
        name: $name, method: $method, path: $path, url: $url, request_body: $data,
        expect: ($expect | tonumber), contains: $contains, auth: ($auth == "1"), stream: ($stream == "1"),
        http_status: ($status | tonumber), curl_exit: ($curl_exit | tonumber), curl_error: $curl_err,
        duration_ms: ($duration | tonumber), response_bytes: ($bytes | tonumber),
        response_excerpt: $body, pass: ($pass == "true"), server_log_tail: $log_tail, at: $at }]' \
      --arg name "${name:-$method $path}" --arg method "$method" --arg path "$path" --arg url "$BASE_URL$path" \
      --arg data "$data" --arg expect "$expect" --arg contains "$contains" --arg auth "$auth" --arg stream "$stream" \
      --arg status "$status" --arg curl_exit "$curl_exit" --arg curl_err "$(head -c 500 "$DIR/.curl_err")" \
      --arg duration "$duration" --arg bytes "$bytes" --arg body "$(head -c 2000 "$DIR/.body" | tr -d '\000')" \
      --arg pass "$pass" --arg log_tail "$log_tail"

    label=$([ "$pass" = true ] && echo PASS || echo FAIL)
    echo "[$label] $method $path -> $status (expect $expect, ${duration}ms, ${bytes} bytes)"
    head -c 600 "$DIR/.body"; echo
    analysis=$(routes_json)
    if [ -n "$analysis" ]; then
      update_state '.affected = ($a | {changed_symbols, routes, required, covered, ok})' --argjson a "$analysis"
      coverage_line "$analysis"
    fi
    [ "$pass" = true ] || { [ -n "$log_tail" ] && printf -- '--- server log tail ---\n%s\n' "$log_tail"; exit 1; }
    ;;

  skip)
    reason=${*:-}
    [ ${#reason} -ge 15 ] || die "give a concrete reason (at least 15 characters)"
    update_state '.waiver = {reason: $reason, at: $at}' --arg reason "$reason"
    echo "waiver recorded for $FP: $reason"
    ;;

  stop)
    stop_server
    echo "server stopped"
    ;;

  *) die "unknown command $cmd (status|start|check|skip|stop)" ;;
esac
