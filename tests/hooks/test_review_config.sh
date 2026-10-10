#!/usr/bin/env bash
# Checks /multi-review reviewer agents and reviewer contract config.
# Run: bash tests/hooks/test_review_config.sh
set -u
cd "$(dirname "$0")/../.."
ANGLES="functionality architecture security scalability tests"
PASSED=0
FAILED=0
check() {  # check <name> <command...>
  local name=$1; shift
  if "$@" >/dev/null 2>&1; then PASSED=$((PASSED + 1)); echo "ok   - $name"
  else FAILED=$((FAILED + 1)); echo "FAIL - $name"; fi
}
body() { awk '/^---$/ { c++; next } c >= 2' "$1"; }

for a in $ANGLES; do
  g=".claude/agents/backend-review-$a.md"
  check "$g refers to contract" grep -q '.claude/skills/multi-review/reviewer-contract.md' "$g"
  check "$g has no shell (tools: Read, Grep, Glob)" grep -qx 'tools: Read, Grep, Glob' "$g"
  check "$g has checklist" test "$(body "$g" | grep -c '^ *- ')" -ge 3
  check "$g has no rubric" bash -c "! grep -q '^- \`critical\`' '$g'"
done
w=.github/workflows/hook-tests.yml
check "workflow checkout does not persist credentials" grep -q 'persist-credentials: false' "$w"
check "workflow watches its own file" test "$(grep -c '".github/workflows/hook-tests.yml"' "$w")" -eq 2
check "skill reviews lockfiles (no lockfile exclusion)" bash -c "! grep -qE 'drop:.*(package-lock|\\*\\.lock)' .claude/skills/multi-review/SKILL.md"
check "skill passes file list as untrusted JSON" grep -q '<untrusted-files>' .claude/skills/multi-review/SKILL.md
check "skill hands reviewers a diff file" grep -q 'DIFF_FILE' .claude/skills/multi-review/SKILL.md
c=.claude/skills/multi-review/reviewer-contract.md
check "contract grants no shell" bash -c "! grep -q 'Run \`git diff' '$c'"
check "contract has rubric" grep -q '^- `critical`' "$c"
check "contract has gate scope section" grep -q '^## PR-creation gate scope' "$c"
check "contract points hook diffs at gate scope" grep -q 'under `.claude/hooks/`.*PR-creation gate scope' "$c"
check "no separate review-instructions dir" test ! -e .claude/review-instructions

echo
echo "passed: $PASSED  failed: $FAILED"
[ "$FAILED" -eq 0 ]
