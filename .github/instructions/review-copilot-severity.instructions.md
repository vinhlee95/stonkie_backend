---
applyTo: "**"
excludeAgent: "cloud-agent"
---

# Code review: which findings to post

For Copilot code review. Local `/multi-review` agents don't read this file; they report every severity.

- Post only `critical`, `high` and `medium` findings, as defined by the severity rubric in `review-guidelines.instructions.md`.
- Don't post `low` findings (nits, naming, minor clarity): no inline comments, no suggestions, and no mention in the review summary or finding count.
- If nothing reaches `medium`, leave no inline comments.
