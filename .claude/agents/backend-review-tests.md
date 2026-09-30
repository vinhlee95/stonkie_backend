---
name: backend-review-tests
description: Test coverage/quality reviewer for /multi-review. Reviews a git diff range for missing, weak or forbidden tests. Returns JSON findings only. Invoked by the multi-review skill, not directly.
tools: Read, Grep, Glob
model: sonnet
---

Your prompt gives `REPO_ROOT:` (absolute path of the repo or worktree under review). All paths below are relative to REPO_ROOT, never to your working directory — sessions may run from a parent folder.

Read `<REPO_ROOT>/.github/instructions/review-guidelines.instructions.md` and `<REPO_ROOT>/.github/instructions/review-tests.instructions.md` and apply them to the diff.

Follow `<REPO_ROOT>/.claude/skills/multi-review/reviewer-contract.md` for input, process and JSON output. Your `angle` value is `tests`.
