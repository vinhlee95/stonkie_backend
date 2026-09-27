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

new_repo_at() {
  mkdir -p "$1" && git -C "$1" init -q && echo a > "$1/f.txt" && git -C "$1" add f.txt \
    && git -C "$1" -c user.email=t@t -c user.name=t commit -qm init && echo "$1"
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
expect "malformed input mentioning gh blocks" 2 "$(echo 'garbage gh pr create' | bash "$HOOK" >/dev/null 2>&1; echo $?)"
expect "malformed input without gh blocks (fail closed)" 2 "$(echo 'garbage' | bash "$HOOK" >/dev/null 2>&1; echo $?)"

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

# 27. commands that only MENTION gh pr create pass through (dirty tree would otherwise block commits)
R=$(new_repo r27); echo dirty >> "$R/f.txt"
expect "commit msg mentioning gh pr create passes" 0 "$(run_hook 'git commit -m "gate gh pr create on review"' "$R")"
expect "grep for gh pr create passes" 0 "$(run_hook "grep -rn 'gh pr create' ." "$R")"
expect "gh issue titled pr create passes" 0 "$(run_hook 'gh issue create --title pr create' "$R")"
expect "echo mentioning gh pr new passes" 0 "$(run_hook 'echo "use gh pr new later"' "$R")"
HEREDOC_CMD=$(printf 'git commit -F - <<EOF\nfix: gate\ngh pr create is now gated\nEOF')
expect "heredoc body mentioning gh pr create passes" 0 "$(run_hook "$HEREDOC_CMD" "$R")"
expect "gh api GET pulls piped to grep -F passes" 0 "$(run_hook 'gh api repos/o/r/pulls > p.json && grep -F foo p.json' "$R")"

# 28. real invocations behind wrappers still block
R=$(new_repo r28)
expect "env-prefixed gh blocks" 2 "$(run_hook 'GH_TOKEN=x gh pr create --fill' "$R")"
expect "command gh blocks" 2 "$(run_hook 'command gh pr create --fill' "$R")"
expect "gh after newline blocks" 2 "$(run_hook "$(printf 'git push\ngh pr create --fill')" "$R")"

# 29. missing jq: non-gh commands still pass, gh pr create blocks
R=$(new_repo r29)
NOJQ_PATH="$TMP/nojq-bin"; mkdir -p "$NOJQ_PATH"
for b in git bash sed awk grep tr cat printf dirname; do p=$(command -v "$b") && ln -sf "$p" "$NOJQ_PATH/$b"; done
nojq() { printf '%s' "$1" | PATH="$NOJQ_PATH" "$(command -v bash)" "$HOOK" >/dev/null 2>&1; echo $?; }
expect "no jq: ls passes" 0 "$(nojq '{"tool_input":{"command":"ls -la"},"cwd":"'"$R"'"}')"
expect "no jq: gh pr create blocks" 2 "$(nojq '{"tool_input":{"command":"gh pr create"},"cwd":"'"$R"'"}')"

# 30. quote-aware splitting: separators inside single quotes are data
R=$(new_repo r30); echo dirty >> "$R/f.txt"
expect "single-quoted backticks in commit msg pass" 0 "$(run_hook 'git commit -m '"'"'fix: `gh pr create` gate'"'"'' "$R")"
expect "single-quoted parens in echo pass" 0 "$(run_hook "echo '(gh pr create)'" "$R")"
expect "here-string word passes" 0 "$(run_hook 'cat <<< "gh pr create"' "$R")"
expect "multi-line quoted commit msg passes" 0 "$(run_hook "$(printf 'git commit -m "fix: gate\n\ngh pr create is gated now"')" "$R")"
expect "double-quoted parens pass" 0 "$(run_hook 'git commit -m "hook (gh pr create gate)"' "$R")"
R=$(new_repo r30b)
expect "command substitution in double quotes blocks" 2 "$(run_hook 'echo "$(gh pr create --fill)"' "$R")"
expect "here-string then gh on next line blocks" 2 "$(run_hook "$(printf 'grep x <<< foo\ngh pr create --fill')" "$R")"

# 31. every gh api write flag family blocks; non-POST method and /pulls/<n> writes pass
R=$(new_repo r31)
expect "gh api --method POST blocks" 2 "$(run_hook 'gh api --method POST repos/o/r/pulls' "$R")"
expect "gh api -F blocks" 2 "$(run_hook 'gh api repos/o/r/pulls -F title=x' "$R")"
expect "gh api --field blocks" 2 "$(run_hook 'gh api repos/o/r/pulls --field title=x' "$R")"
expect "gh api --raw-field blocks" 2 "$(run_hook 'gh api repos/o/r/pulls --raw-field title=x' "$R")"
expect "gh api --input blocks" 2 "$(run_hook 'gh api repos/o/r/pulls --input body.json' "$R")"
expect "gh api --method GET passes" 0 "$(run_hook 'gh api --method GET repos/o/r/pulls' "$R")"
expect "gh api /pulls/12/comments write passes" 0 "$(run_hook 'gh api repos/o/r/pulls/12/comments -f body=x' "$R")"
expect "gh api /pulls?per_page write blocks" 2 "$(run_hook 'gh api repos/o/r/pulls?x=1 -f title=x' "$R")"

# 32. critical severity blocks and can be waived; more wrappers block
CRIT='[{"id":"SEC-1","angles":["security"],"severity":"critical","file":"f.txt","line":1,"title":"t","detail":"d","suggestion":"s"}]'
R=$(new_repo r32); write_state "$R" "$(head_sha "$R")" "$ALL_OK" "$CRIT" '[]'
expect "unwaived critical blocks" 2 "$(run_hook 'gh pr create --fill' "$R")"
R=$(new_repo r32b); write_state "$R" "$(head_sha "$R")" "$ALL_OK" "$CRIT" '[{"id":"SEC-1","reason":"fp"}]'
expect "waived critical allows" 0 "$(run_hook 'gh pr create --fill' "$R")"
R=$(new_repo r32c)
for c in 'sudo gh pr create --fill' 'nohup gh pr create --fill' 'exec gh pr create --fill' 'time gh pr create --fill' 'env GH_TOKEN=x gh pr create' 'bash -lc "gh pr create --fill"'; do
  expect "wrapper blocks: $c" 2 "$(run_hook "$c" "$R")"
done

# 33. backslash-newline continuation joins lines like the shell does
R=$(new_repo r33)
expect "continued gh pr \\ create blocks" 2 "$(run_hook "$(printf 'gh pr \\\ncreate --fill')" "$R")"
expect "continued gh \\ pr create blocks" 2 "$(run_hook "$(printf 'gh \\\npr create')" "$R")"
R=$(new_repo r33b); echo dirty >> "$R/f.txt"
expect "continued non-gh command passes" 0 "$(run_hook "$(printf 'git commit \\\n-m x')" "$R")"

# 34. JSON \u escapes can't hide gh from the pre-filter
R=$(new_repo r34)
raw() { printf '%s' "$1" | bash "$HOOK" >/dev/null 2>&1; echo $?; }
NOGH="$TMP/plain-$$"; R=$(new_repo_at "$NOGH/repo")
expect "unicode-escaped gh blocks" 2 "$(raw '{"tool_input":{"command":"\u0067\u0068 pr create"},"cwd":"'"$R"'"}')"

# 35. command substitutions nested in double quotes are commands
R=$(new_repo r35)
expect "nested quotes inside \$() block" 2 "$(run_hook 'echo "$(gh pr "create" --fill)"' "$R")"
expect "\$() after quoted text blocks" 2 "$(run_hook 'echo "a $(echo "b" && gh pr create) c"' "$R")"
expect "backtick in double quotes blocks" 2 "$(run_hook 'echo "x `gh pr create` y"' "$R")"
expect "here-string with \$() blocks" 2 "$(run_hook 'cat <<< "$(gh pr create --fill)"' "$R")"

# 36. shell control prefixes and more wrappers
R=$(new_repo r36)
for c in 'if gh pr create; then :; fi' '! gh pr create' 'while gh pr create; do break; done' '{ gh pr create; }' \
         'eval gh pr create' 'eval "gh pr create"' 'timeout 5 gh pr create' 'nice -n 10 gh pr create' \
         'sudo -E gh pr create' 'time -p gh pr create' 'echo | xargs -r gh pr create'; do
  expect "control/wrapper blocks: $c" 2 "$(run_hook "$c" "$R")"
done

# 37. line continuation inside a word, quoted or not
R=$(new_repo r37)
expect "quoted word continuation blocks" 2 "$(run_hook "$(printf 'gh pr "cr\\\neate"')" "$R")"
expect "unquoted word continuation blocks" 2 "$(run_hook "$(printf 'gh pr cr\\\neate')" "$R")"

# 38. unterminated heredoc: its body is still inspected
R=$(new_repo r38)
expect "unterminated heredoc body blocks" 2 "$(run_hook "$(printf 'cat <<EOF\ngh pr create --fill')" "$R")"

# 39. PR target: cd, -R/--repo, --head must match the reviewed repo/branch
R=$(new_repo r39); write_state "$R" "$(head_sha "$R")" "$ALL_OK" '[]' '[]'
git -C "$R" remote add origin git@github.com:me/r39.git
BR=$(git -C "$R" rev-parse --abbrev-ref HEAD)
S=$(new_repo r39s)
expect "cd into unreviewed repo blocks" 2 "$(run_hook "cd $S && gh pr create --fill" "$R")"
expect "cd into reviewed repo allows" 0 "$(run_hook "cd $R && gh pr create --fill" "$S")"
expect "-R matching origin allows" 0 "$(run_hook 'gh -R me/r39 pr create --fill' "$R")"
expect "--repo other blocks" 2 "$(run_hook 'gh pr create --repo other/x --fill' "$R")"
expect "--head current branch allows" 0 "$(run_hook "gh pr create --head $BR --fill" "$R")"
expect "--head owner:current allows" 0 "$(run_hook "gh pr create --head me:$BR --fill" "$R")"
expect "--head other branch blocks" 2 "$(run_hook 'gh pr create --head other-branch --fill' "$R")"
expect "-H other branch blocks" 2 "$(run_hook 'gh pr create -H other-branch' "$R")"

# 40. ANSI-C quoted command names are decoded
R=$(new_repo r40)
expect "ansi-c hex gh blocks" 2 "$(run_hook "\$'\\x67\\x68' pr create --fill" "$R")"
expect "ansi-c octal gh blocks" 2 "$(run_hook "\$'\\147\\150' pr create" "$R")"
expect "ansi-c unicode gh blocks" 2 "$(run_hook "\$'\\u0067h' pr create" "$R")"
expect "ansi-c string as echo arg passes" 0 "$(run_hook "echo \$'gh pr create'" "$R")"

# 41. heredoc bodies: unquoted delimiter runs $( ), quoted delimiter is literal
R=$(new_repo r41)
expect "heredoc body \$() blocks" 2 "$(run_hook "$(printf 'cat <<EOF\n$(gh pr create --fill)\nEOF')" "$R")"
expect "heredoc body backtick blocks" 2 "$(run_hook "$(printf 'cat <<EOF\nx `gh pr create` y\nEOF')" "$R")"
expect "quoted-delimiter heredoc body is literal" 0 "$(run_hook "$(printf "cat <<'EOF'\n\$(gh pr create --fill)\nEOF")" "$R")"

# 42. redirections anywhere in the command
R=$(new_repo r42)
expect "leading redirect blocks" 2 "$(run_hook '>out gh pr create --fill' "$R")"
expect "leading fd redirect blocks" 2 "$(run_hook '2>/dev/null gh pr create --fill' "$R")"
expect "trailing redirect blocks" 2 "$(run_hook 'gh pr create --fill > out.txt 2>&1' "$R")"
expect "process substitution blocks" 2 "$(run_hook 'cat <(gh pr create --fill)' "$R")"
expect "redirect target text passes" 0 "$(run_hook 'echo hi > "gh pr create.txt"' "$R")"

# 43. dynamic gh api method/endpoint fail closed
R=$(new_repo r43)
expect "gh api dynamic method on pulls blocks" 2 "$(run_hook 'METHOD=POST; gh api --method "$METHOD" repos/o/r/pulls' "$R")"
expect "gh api dynamic endpoint write blocks" 2 "$(run_hook 'gh api "$URL" -f title=x' "$R")"
expect "gh api lowercase post blocks" 2 "$(run_hook 'gh api --method post repos/o/r/pulls' "$R")"
expect "gh api dynamic GET passes" 0 "$(run_hook 'gh api "$URL"' "$R")"

# 44. wrapper options that take a value
R=$(new_repo r44)
for c in 'sudo -u build-user gh pr create --fill' 'timeout -s KILL 5 gh pr create' 'xargs -I {} gh pr create' 'env -u FOO gh pr create'; do
  expect "wrapper with operand blocks: $c" 2 "$(run_hook "$c" "$R")"
done

# 45. any shell's -c runs its argument as a command
R=$(new_repo r45)
for c in "dash -c 'gh pr create'" "/bin/dash -c 'gh pr create'" "ksh -c 'gh pr create'" "bash -e -c 'gh pr create'" \
         "sh -xc 'gh pr create'" "fish -c 'gh pr create'" "busybox sh -c 'gh pr create'"; do
  expect "shell -c blocks: $c" 2 "$(run_hook "$c" "$R")"
done
expect "shell script arg passes" 0 "$(run_hook "dash ./build.sh" "$R")"

# 46. escapes/quotes/expansions that build the gh command name
R=$(new_repo r46)
for c in 'g\h pr create --fill' "g''h pr create" '"g"h pr create' 'G=gh; $G pr create' '$(echo gh) pr create' 'gh api graphql -f query="mutation{createPullRequest(input:{})}"'; do
  expect "built command name blocks: $c" 2 "$(run_hook "$c" "$R")"
done

# 47. cd targets: quoted paths are followed, dynamic/unknown ones fail closed
R=$(new_repo r47); write_state "$R" "$(head_sha "$R")" "$ALL_OK" '[]' '[]'
SPACED=$(new_repo_at "$TMP/with space/repo")
expect "cd quoted path into unreviewed repo blocks" 2 "$(run_hook "cd \"$SPACED\" && gh pr create --fill" "$R")"
expect "cd to variable blocks" 2 "$(run_hook 'cd "$DIR" && gh pr create --fill' "$R")"
expect "cd - blocks" 2 "$(run_hook 'cd - && gh pr create --fill' "$R")"

# 48. PR target repo/owner/base must be the reviewed ones
R=$(new_repo r48); write_state "$R" "$(head_sha "$R")" "$ALL_OK" '[]' '[]'
git -C "$R" remote add origin git@github.com:me/r48.git
BR=$(git -C "$R" rev-parse --abbrev-ref HEAD)
expect "gh api pulls in other repo blocks" 2 "$(run_hook 'gh api repos/attacker/other/pulls -f title=x' "$R")"
expect "gh api pulls in own repo allows" 0 "$(run_hook 'gh api repos/me/r48/pulls -f title=x' "$R")"
expect "--head other-owner:branch blocks" 2 "$(run_hook "gh pr create --head other-owner:$BR --fill" "$R")"
expect "--base default branch allows" 0 "$(run_hook 'gh pr create --base main --fill' "$R")"
expect "--base other branch blocks" 2 "$(run_hook 'gh pr create --base develop --fill' "$R")"
expect "-B other branch blocks" 2 "$(run_hook 'gh pr create -B develop --fill' "$R")"
expect "gh api base other branch blocks" 2 "$(run_hook 'gh api repos/me/r48/pulls -f base=develop -f title=x' "$R")"

# 49. brace expansion builds command words
R=$(new_repo r49)
for c in '{gh,pr,create}' '{gh,pr,create} --fill' '{gh,} pr create' 'gh pr {create,}' '{{gh,pr},create}'; do
  expect "brace expansion blocks: $c" 2 "$(run_hook "$c" "$R")"
done
expect "brace in unrelated command passes" 0 "$(run_hook 'echo {a,b}' "$R")"

echo
echo "passed: $PASSED  failed: $FAILED"
[ "$FAILED" -eq 0 ]
