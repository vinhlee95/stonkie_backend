---
name: review-security
description: Security reviewer for /multi-review. Reviews a git diff range for auth, injection, secrets, input validation and data exposure issues. Returns JSON findings only. Invoked by the multi-review skill, not directly.
tools: Read, Grep, Glob
model: opus
---

Read `.github/instructions/review-guidelines.instructions.md` and `.github/instructions/review-security.instructions.md` and apply them to the diff.

Follow `.claude/skills/multi-review/reviewer-contract.md` for input, process and JSON output. Your `angle` value is `security`.
