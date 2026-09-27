#!/usr/bin/env bash
# Checks that Copilot review instructions and local /multi-review agents share one source.
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
FRONTMATTER=$'---\napplyTo: "**"\nexcludeAgent: "cloud-agent"\n---'
body() { awk '/^---$/ { c++; next } c >= 2' "$1"; }

for f in guidelines $ANGLES; do
  p=".github/instructions/review-$f.instructions.md"
  check "$p exists" test -f "$p"
  check "$p frontmatter" test "$(head -4 "$p" 2>/dev/null)" = "$FRONTMATTER"
  check "$p tool-neutral" bash -c "! grep -qE 'reviewer-contract|angle. value|JSON array|\bGrep\b|INTENT|\.\./CLAUDE' '$p'"
done
for a in $ANGLES; do
  p=".github/instructions/review-$a.instructions.md"
  check "$p has checklist" test "$(grep -c '^ *- ' "$p" 2>/dev/null || echo 0)" -ge 3
  g=".claude/agents/review-$a.md"
  check "$g refers to guidelines" grep -q '.github/instructions/review-guidelines.instructions.md' "$g"
  check "$g refers to its angle file" grep -q ".github/instructions/review-$a.instructions.md" "$g"
  check "$g has no shell (tools: Read, Grep, Glob)" grep -qx 'tools: Read, Grep, Glob' "$g"
  check "$g has no checklist" test "$(body "$g" | grep -c '^ *- ')" -eq 0
done
w=.github/workflows/hook-tests.yml
check "workflow checkout does not persist credentials" grep -q 'persist-credentials: false' "$w"
check "workflow watches its own file" test "$(grep -c '".github/workflows/hook-tests.yml"' "$w")" -eq 2
check "skill hands reviewers a diff file" grep -q 'DIFF_FILE' .claude/skills/multi-review/SKILL.md
c=.claude/skills/multi-review/reviewer-contract.md
check "contract grants no shell" bash -c "! grep -q 'Run \`git diff' '$c'"
check "contract refers to guidelines" grep -q '.github/instructions/review-guidelines.instructions.md' "$c"
check "contract has no rubric" bash -c "! grep -q '^- \`critical\`' '$c'"
check "guidelines has rubric" grep -q '^- `critical`' .github/instructions/review-guidelines.instructions.md

echo
echo "passed: $PASSED  failed: $FAILED"
[ "$FAILED" -eq 0 ]
