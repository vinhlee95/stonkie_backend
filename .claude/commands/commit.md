Run a pre-commit review on staged changes, then commit if all checks pass.

Commit message argument: $ARGUMENTS

## Steps

### 1. Stage and get diff
Run `git add -A` to stage all changes, then run `git diff --cached` and `git diff --cached --name-only` to see what's staged.
If nothing is staged after adding, tell the user and stop.

### 2. Security review
Inspect the diff for:
- Hardcoded secrets, API keys, passwords, tokens (patterns like `sk-`, `AIza`, `Bearer `, `password =`, etc.)
- SQL injection risks (raw string interpolation into queries)
- Command injection (user input passed to shell commands)
- XSS risks (unescaped user content rendered as HTML)
- Exposed internal paths, credentials, or PII in logs

If any are found, list them clearly and **stop** — do not commit.

### 3. Debug artifact scan
Check for:
- `print(` statements in Python files (outside of test files)
- `pdb.set_trace()` or `breakpoint()` calls
- TODO/FIXME comments in changed lines
- `console.log` in JS/TS files

Report findings. For TODOs, ask if they're intentional before proceeding. For print/breakpoint, flag as blocking.

### 4. Architecture conformance (from @CLAUDE.md)
Check the diff conforms to the 3-layer architecture:
- **Presentation layer** (`main.py`): Only routing/validation, no business logic
- **Business logic** (`services/`, `agent/`): No direct DB calls, no FastAPI imports
- **Data access** (`connectors/`, `models/`): No business logic
- Type hints used on new/modified function signatures
- `StrEnum` used for new string enums
- Database sessions use context managers (`with SessionLocal() as db`)
- No new helpers/abstractions created for one-time operations

Report any violations.

### 5. Linting
Run: `source venv/bin/activate && ruff check $(git diff --cached --name-only | grep '\.py$' | tr '\n' ' ')`

If linting errors found, show them and ask user to fix before committing. Offer to auto-fix with `ruff check --fix`.

### 6. Healthcheck test
Run: `source venv/bin/activate && python -m pytest tests/test_healthcheck.py -v`

If tests fail, show output and stop.

### 7. Commit message
If `$ARGUMENTS` is provided, use it as the commit message.
If not provided, generate a concise conventional commit message based on the diff:
- Format: `<type>(<scope>): <description>` where type is one of: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`
- Keep under 72 chars
- Be specific about what changed, not how

Show the message to the user and ask for confirmation before committing.

### 8. Commit
Run the commit with the confirmed message:
```
git commit -m "$(cat <<'EOF'
<message>

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>
EOF
)"
```

Report success and show the commit hash.
