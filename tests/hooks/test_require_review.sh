#!/usr/bin/env bash
# Tests for .claude/hooks/require-review.sh
# Run: bash tests/hooks/test_require_review.sh
set -u

HOOK="$(cd "$(dirname "$0")/../.." && pwd)/.claude/hooks/require-review.sh"
TMP=$(mktemp -d)  # left for the OS to clean; no rm -rf per repo rules
PASSED=0
FAILED=0

new_repo() {
  local repo="$TMP/$1"
  mkdir -p "$repo"
  git -C "$repo" init -q
  echo a > "$repo/f.txt"
  git -C "$repo" add f.txt
  git -C "$repo" -c user.email=t@t -c user.name=t commit -qm init
  mkdir -p "$repo/.claude/review-state"
  echo "$repo"
}

head_sha() { git -C "$1" rev-parse HEAD; }

# write_state <repo> <sha> <angles-json> <findings-json> <waivers-json>
write_state() {
  jq -n --arg sha "$2" --argjson angles "$3" --argjson findings "$4" --argjson waivers "$5" \
    '{sha: $sha, timestamp: "2026-01-01T00:00:00Z", range: "origin/main...HEAD",
      angles: $angles, findings: $findings, waivers: $waivers, status: "pass"}' \
    > "$1/.claude/review-state/$2.json"
}

ALL_OK='{"functionality":"ok","architecture":"ok","security":"ok","scalability":"ok","tests":"ok"}'
HIGH='[{"id":"SEC-1","angles":["security"],"severity":"high","file":"f.txt","line":1,"title":"t","detail":"d","suggestion":"s"}]'
MEDIUM='[{"id":"ARCH-1","angles":["architecture"],"severity":"medium","file":"f.txt","line":1,"title":"t","detail":"d","suggestion":"s"}]'

# run_hook <command> <cwd> -> prints exit code
run_hook() {
  jq -n --arg c "$1" --arg cwd "$2" '{tool_name: "Bash", tool_input: {command: $c}, cwd: $cwd}' \
    | bash "$HOOK" >/dev/null 2>&1
  echo $?
}

expect() {  # expect <name> <expected-exit> <actual-exit>
  if [ "$2" = "$3" ]; then PASSED=$((PASSED + 1)); echo "ok   - $1"
  else FAILED=$((FAILED + 1)); echo "FAIL - $1 (expected $2, got $3)"; fi
}

# 1. non-PR command passes through, even outside a git repo
expect "non-gh command passthrough" 0 "$(run_hook 'ls -la' "$TMP")"

# 2. other gh pr subcommands pass through
R=$(new_repo r2)
expect "gh pr view passthrough" 0 "$(run_hook 'gh pr view 12' "$R")"

# 3. no state file -> block
R=$(new_repo r3)
expect "missing review blocks" 2 "$(run_hook 'gh pr create --fill' "$R")"

# 4. passing state -> allow
R=$(new_repo r4); write_state "$R" "$(head_sha "$R")" "$ALL_OK" '[]' '[]'
expect "passing review allows" 0 "$(run_hook 'gh pr create --fill' "$R")"

# 5. medium-only findings -> allow
R=$(new_repo r5); write_state "$R" "$(head_sha "$R")" "$ALL_OK" "$MEDIUM" '[]'
expect "medium finding allows" 0 "$(run_hook 'gh pr create --fill' "$R")"

# 6. unwaived high -> block (even though .status says pass)
R=$(new_repo r6); write_state "$R" "$(head_sha "$R")" "$ALL_OK" "$HIGH" '[]'
expect "unwaived high blocks" 2 "$(run_hook 'gh pr create --fill' "$R")"

# 7. waived high -> allow
R=$(new_repo r7); write_state "$R" "$(head_sha "$R")" "$ALL_OK" "$HIGH" '[{"id":"SEC-1","reason":"false positive"}]'
expect "waived high allows" 0 "$(run_hook 'gh pr create --fill' "$R")"

# 8. errored angle -> block
R=$(new_repo r8)
write_state "$R" "$(head_sha "$R")" '{"functionality":"ok","architecture":"ok","security":"errored","scalability":"ok","tests":"ok"}' '[]' '[]'
expect "errored angle blocks" 2 "$(run_hook 'gh pr create --fill' "$R")"

# 9. missing angle -> block
R=$(new_repo r9)
write_state "$R" "$(head_sha "$R")" '{"functionality":"ok","architecture":"ok","security":"ok","scalability":"ok"}' '[]' '[]'
expect "missing angle blocks" 2 "$(run_hook 'gh pr create --fill' "$R")"

# 10. stale review (new commit after review) -> block
R=$(new_repo r10); write_state "$R" "$(head_sha "$R")" "$ALL_OK" '[]' '[]'
echo b >> "$R/f.txt"; git -C "$R" -c user.email=t@t -c user.name=t commit -qam second
expect "stale review blocks" 2 "$(run_hook 'gh pr create --fill' "$R")"

# 11. dirty tracked file -> block
R=$(new_repo r11); write_state "$R" "$(head_sha "$R")" "$ALL_OK" '[]' '[]'
echo dirty >> "$R/f.txt"
expect "dirty tree blocks" 2 "$(run_hook 'gh pr create --fill' "$R")"

# 12. untracked file only -> allow
R=$(new_repo r12); write_state "$R" "$(head_sha "$R")" "$ALL_OK" '[]' '[]'
echo x > "$R/untracked.txt"
expect "untracked file allows" 0 "$(run_hook 'gh pr create --fill' "$R")"

# 13. malformed state JSON -> block
R=$(new_repo r13); echo '{not json' > "$R/.claude/review-state/$(head_sha "$R").json"
expect "malformed state blocks" 2 "$(run_hook 'gh pr create --fill' "$R")"

# 14. compound command -> block
R=$(new_repo r14)
expect "compound command blocks" 2 "$(run_hook 'git push -u origin HEAD && gh pr create --fill' "$R")"

# 15. gh pr create outside a git repo -> block (fail closed)
mkdir -p "$TMP/notrepo"
expect "non-repo cwd blocks" 2 "$(run_hook 'gh pr create --fill' "$TMP/notrepo")"

# 16. cwd is a subdirectory -> resolves toplevel state
R=$(new_repo r16); write_state "$R" "$(head_sha "$R")" "$ALL_OK" '[]' '[]'
mkdir -p "$R/sub/dir"
expect "subdirectory cwd allows" 0 "$(run_hook 'gh pr create --fill' "$R/sub/dir")"

# 17. git worktree -> uses worktree's own state dir
R=$(new_repo r17)
git -C "$R" worktree add -q -b wt "$TMP/r17-wt"
mkdir -p "$TMP/r17-wt/.claude/review-state"
expect "worktree without review blocks" 2 "$(run_hook 'gh pr create --fill' "$TMP/r17-wt")"
write_state "$TMP/r17-wt" "$(head_sha "$TMP/r17-wt")" "$ALL_OK" '[]' '[]'
expect "worktree with review allows" 0 "$(run_hook 'gh pr create --fill' "$TMP/r17-wt")"

# 18. malformed hook input -> block
expect "malformed input blocks" 2 "$(echo 'garbage' | bash "$HOOK" >/dev/null 2>&1; echo $?)"

# 19. `gh pr new` alias -> block
R=$(new_repo r19)
expect "gh pr new alias blocks" 2 "$(run_hook 'gh pr new --fill' "$R")"

# 20. path-qualified gh -> block
R=$(new_repo r20)
expect "path-qualified gh blocks" 2 "$(run_hook '/opt/homebrew/bin/gh pr create --fill' "$R")"

# 21. global flags before subcommand -> block
R=$(new_repo r21)
expect "gh -R flag blocks" 2 "$(run_hook 'gh -R owner/repo pr create --fill' "$R")"
expect "gh --repo flag blocks" 2 "$(run_hook 'gh --repo owner/repo pr create --fill' "$R")"

# 22. gh api POST to pulls -> block
R=$(new_repo r22)
expect "gh api pulls POST blocks" 2 "$(run_hook 'gh api -X POST repos/o/r/pulls -f title=x' "$R")"

# 23. unrelated gh commands still pass
R=$(new_repo r23)
expect "gh pr list passthrough" 0 "$(run_hook 'gh pr list --state open' "$R")"
expect "gh api GET pulls passthrough" 0 "$(run_hook 'gh api repos/o/r/pulls' "$R")"

# 24. gh pr create followed by non-space separators / inside subshells -> block
R=$(new_repo r24)
for c in 'gh pr create;echo done' 'gh pr create&&echo done' 'gh pr create|tee x' 'gh pr create>out' \
         '(gh pr create)' '$(gh pr create)' '`gh pr create`' 'bash -c "gh pr create"' "bash -c 'gh pr create'" \
         '"gh" pr create' 'gh pr "create"'; do
  expect "blocks: $c" 2 "$(run_hook "$c" "$R")"
done

# 25. gh api POST with attached field value -> block
R=$(new_repo r25)
expect "gh api -fhead=x blocks" 2 "$(run_hook 'gh api repos/o/r/pulls -fhead=x' "$R")"

# 26. near-misses still pass
R=$(new_repo r26)
expect "gh pr created-list passthrough" 0 "$(run_hook 'gh pr view created' "$R")"
expect "gh pr checks passthrough" 0 "$(run_hook 'gh pr checks 12' "$R")"

echo
echo "passed: $PASSED  failed: $FAILED"
[ "$FAILED" -eq 0 ]
