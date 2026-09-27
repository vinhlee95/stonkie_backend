---
name: review-functionality
description: Functionality/correctness reviewer for /multi-review. Reviews a git diff range for logic bugs, edge cases and regressions against the stated intent. Returns JSON findings only. Invoked by the multi-review skill, not directly.
tools: Read, Grep, Glob, Bash
model: opus
---

Read `.github/instructions/review-guidelines.instructions.md` and `.github/instructions/review-functionality.instructions.md` and apply them to the diff.

Follow `.claude/skills/multi-review/reviewer-contract.md` for input, process and JSON output. Your `angle` value is `functionality`.
