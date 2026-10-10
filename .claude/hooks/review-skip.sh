#!/usr/bin/env bash
# UserPromptSubmit hook: when the user's own prompt says "skip review" (also "skip local review",
# "skip multi-review", "skip-review"), mark this session so require-review.sh lets gh pr create
# through without a /multi-review. Lasts for the rest of the session.
# Only a real user prompt fires this hook, so Claude cannot trigger the skip from tool output.
# A negated phrase ("don't skip review", "never skip review") does not count.
set -uo pipefail

input=$(cat)
command -v jq >/dev/null 2>&1 || exit 0

prompt=$(printf '%s' "$input" | jq -r '.prompt // ""' 2>/dev/null) || exit 0
sid=$(printf '%s' "$input" | jq -r '.session_id // ""' 2>/dev/null) || exit 0
[[ $sid =~ ^[A-Za-z0-9_-]+$ ]] || exit 0

shopt -s nocasematch
skip_re='(^|[^[:alnum:]_-])skip[[:space:]-]+((the|local|multi)[[:space:]-]*)*review([^[:alnum:]_-]|$)'
negated_re='(^|[^[:alnum:]_])(don.?t|do[[:space:]]+not|never|not|no)[[:space:]]+skip[[:space:]-]'
[[ $prompt =~ $skip_re ]] || exit 0
[[ $prompt =~ $negated_re ]] && exit 0

dir="${CLAUDE_REVIEW_SKIP_DIR:-$HOME/.claude/review-skip}"
mkdir -p "$dir" && : > "$dir/$sid" || exit 0
# stdout of a UserPromptSubmit hook is added to Claude's context
echo "Review gate skipped for this session at the user's request: gh pr create no longer needs /multi-review here. Do not run /multi-review unless the user asks."
exit 0
