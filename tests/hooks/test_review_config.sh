#!/usr/bin/env bash
# Checks that shared review instructions and local /multi-review agents stay in sync.
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

for f in guidelines $ANGLES; do
  p=".claude/review-instructions/$f.md"
  check "$p exists" test -f "$p"
  check "$p has no frontmatter" bash -c "! head -1 '$p' | grep -qx -- ---"
  check "$p tool-neutral" bash -c "! grep -qE 'reviewer-contract|angle. value|JSON array|\bGrep\b|INTENT|\.\./CLAUDE' '$p'"
done
for a in $ANGLES; do
  p=".claude/review-instructions/$a.md"
  check "$p has checklist" test "$(grep -c '^ *- ' "$p" 2>/dev/null || echo 0)" -ge 3
  g=".claude/agents/backend-review-$a.md"
  check "$g refers to guidelines" grep -q '.claude/review-instructions/guidelines.md' "$g"
  check "$g refers to its angle file" grep -q ".claude/review-instructions/$a.md" "$g"
  check "$g has no shell (tools: Read, Grep, Glob)" grep -qx 'tools: Read, Grep, Glob' "$g"
  check "$g has no checklist" test "$(body "$g" | grep -c '^ *- ')" -eq 0
done
w=.github/workflows/hook-tests.yml
check "workflow checkout does not persist credentials" grep -q 'persist-credentials: false' "$w"
check "workflow watches its own file" test "$(grep -c '".github/workflows/hook-tests.yml"' "$w")" -eq 2
check "skill reviews lockfiles (no lockfile exclusion)" bash -c "! grep -qE 'drop:.*(package-lock|\\*\\.lock)' .claude/skills/multi-review/SKILL.md"
check "skill passes file list as untrusted JSON" grep -q '<untrusted-files>' .claude/skills/multi-review/SKILL.md
check "skill hands reviewers a diff file" grep -q 'DIFF_FILE' .claude/skills/multi-review/SKILL.md
c=.claude/skills/multi-review/reviewer-contract.md
check "contract grants no shell" bash -c "! grep -q 'Run \`git diff' '$c'"
check "contract refers to guidelines" grep -q '.claude/review-instructions/guidelines.md' "$c"
check "contract has no rubric" bash -c "! grep -q '^- \`critical\`' '$c'"
check "contract points hook diffs at gate-scope" grep -q '.claude/review-instructions/gate-scope.md' "$c"
check "gate-scope exists" test -f .claude/review-instructions/gate-scope.md
check "guidelines require an angle label on every comment" grep -qF '[<severity> · <angle>]' .claude/review-instructions/guidelines.md
check "guidelines has rubric" grep -q '^- `critical`' .claude/review-instructions/guidelines.md

echo
echo "passed: $PASSED  failed: $FAILED"
[ "$FAILED" -eq 0 ]
