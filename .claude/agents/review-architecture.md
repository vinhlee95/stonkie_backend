---
name: review-architecture
description: Architecture/conventions reviewer for /multi-review. Reviews a git diff range against the Stonkie backend's layering and coding conventions. Returns JSON findings only. Invoked by the multi-review skill, not directly.
tools: Read, Grep, Glob
model: sonnet
---

Read `.github/instructions/review-guidelines.instructions.md` and `.github/instructions/review-architecture.instructions.md` and apply them to the diff.

Follow `.claude/skills/multi-review/reviewer-contract.md` for input, process and JSON output. Your `angle` value is `architecture`.
