# Code review: scope of the PR-creation gate

`.claude/hooks/require-review.sh` is a workflow guardrail for Claude Code sessions: it stops an agent from *forgetting* to run `/multi-review` before `gh pr create`. It is not a security boundary against a deliberate attacker — the review state is a local file the same user controls, and no shell-text hook can model all of Bash.

When reviewing this hook:

- Report bypasses that a normal workflow could plausibly produce: common `gh` flags, options and environment variables (e.g. `--repo`, `--head`, `--base`, `GH_REPO`), ordinary shell structure (`&&`, pipes, subshells, `cd`), common wrappers (`env`, `sudo`, `bash -c`), and scripts that call `gh`.
- Do not report deliberately obfuscated invocations whose only purpose is to evade the gate (for example building `gh`, `pr` or `create` from `$(printf …)`, character escapes, brace/glob tricks, or encoded payloads). These are out of scope.
- Correctness bugs that block ordinary commands (false positives) are in scope and worth reporting.
