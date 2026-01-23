---
name: verify-changes
description: REQUIRED pre-commit verification - MUST run tests, type checks, and linting before any git commit
when_to_use:
  - Before creating any git commits
  - After making API endpoint changes
  - After modifying service layer logic
  - After database model changes
  - When requested by user with /verify-changes
  - When pre-commit hooks fail and you need to verify fixes
allowed_tools:
  - bash
---

# Verify Changes Skill

**REQUIRED: This skill MUST be run before creating any git commits.**

This skill automates the code verification workflow documented in CLAUDE.md. It runs tests, type checks, and linting to ensure code quality before committing changes.

> **⚠️ IMPORTANT:** Do not commit code without running verification first. Always run at minimum the Quick Verification (healthcheck + ruff checks) before any commit.

> **📦 Virtual Environment:** All pytest commands require the virtual environment to be activated. Commands use `source venv/bin/activate &&` to ensure dependencies are available.

## Quick Verification (Recommended)

For most changes, run these three checks:

### 1. Healthcheck Test
```bash
# Activate virtual environment and run test
source venv/bin/activate && python -m pytest tests/test_healthcheck.py -v
```

This critical test ensures:
- FastAPI app imports and initializes correctly
- No breaking changes to core dependencies
- API endpoints remain accessible

### 2. Ruff Linting
```bash
ruff check .
```

Checks for:
- Python syntax errors (E)
- Pyflakes issues (F)
- Import organization (I)

### 3. Format Check
```bash
ruff format --check .
```

Verifies code formatting without modifying files.

---

## Full Verification

For significant changes or before final commit:

### 1. All Tests
```bash
# Activate virtual environment and run all tests
source venv/bin/activate && python -m pytest tests/ -v
```

Runs the complete test suite to catch any regressions.

### 2. Comprehensive Lint Check
```bash
ruff check . --output-format=full
```

Shows detailed linting results with context.

### 3. Auto-Format (if needed)
```bash
ruff format .
```

Automatically formats all Python files.

---

## Context-Aware Verification

Choose verification level based on change type:

### API Endpoint Changes
```bash
# 1. Run healthcheck
source venv/bin/activate && python -m pytest tests/test_healthcheck.py -v

# 2. Run all tests
source venv/bin/activate && python -m pytest tests/ -v

# 3. Optional: Test endpoint locally
# Start server in one terminal:
hypercorn main:app --bind localhost:8080 --reload

# In another terminal:
curl -X GET http://localhost:8080/api/healthcheck
```

### Service/Logic Changes
```bash
# 1. Run healthcheck
source venv/bin/activate && python -m pytest tests/test_healthcheck.py -v

# 2. Run specific test file (if exists)
source venv/bin/activate && python -m pytest tests/test_<module>.py -v

# 3. Run all tests
source venv/bin/activate && python -m pytest tests/ -v
```

### Model/Database Changes
```bash
# 1. Run healthcheck
source venv/bin/activate && python -m pytest tests/test_healthcheck.py -v

# 2. Test migration
source venv/bin/activate && alembic upgrade head

# 3. Test rollback
source venv/bin/activate && alembic downgrade -1 && alembic upgrade head

# 4. Run all tests
source venv/bin/activate && python -m pytest tests/ -v
```

### Import/Organization Changes
```bash
# 1. Format check
ruff format --check .

# 2. Lint check
ruff check .

# 3. Healthcheck test
source venv/bin/activate && python -m pytest tests/test_healthcheck.py -v
```

---

## Error Handling Guide

### Pytest Failures

**If healthcheck test fails:**
1. Check the error message for import errors or missing dependencies
2. Verify database connection (if DB-related)
3. Ensure environment variables are set
4. Try running the full test suite to identify scope

**If other tests fail:**
1. Read the failure message carefully
2. Check if related to your changes
3. Run the specific test file with `-v` for details
4. Check test fixtures and setup

### Ruff Issues

**Auto-fix linting issues:**
```bash
ruff check --fix .
```

**Auto-format code:**
```bash
ruff format .
```

**Common issues:**
- Unused imports: Remove or comment with `# noqa: F401`
- Import order: Let ruff auto-fix with `--fix`
- Line too long: Ruff format will handle most cases
- Syntax errors: Review the error location and fix manually

### Dependencies Missing

**If worktree lacks dependencies:**
```bash
# Option 1: Install in worktree
pip install -r requirements.txt

# Option 2: Verify syntax only
python -m py_compile <file_path>
```

---

## Verification Checklist

Use this checklist to track verification progress:

- [ ] Healthcheck test passes (`source venv/bin/activate && pytest tests/test_healthcheck.py -v`)
- [ ] Ruff linting passes (`ruff check .`)
- [ ] Format check passes (`ruff format --check .`)
- [ ] All tests pass (`source venv/bin/activate && pytest tests/ -v`) _(if needed)_
- [ ] Local server testing complete _(if API changes)_
- [ ] Database migration verified _(if model changes)_
- [ ] Code follows project conventions
- [ ] No breaking changes to existing functionality
- [ ] Changes align with task requirements

---

## Summary Report Template

After running verification, provide a summary:

```
✓ Verification Results

Healthcheck Test: [PASS/FAIL]
Ruff Linting: [PASS/FAIL]
Format Check: [PASS/FAIL]
Full Test Suite: [PASS/FAIL/SKIPPED]

Issues Found: [number or "None"]
[List any issues with file:line references]

Status: [READY TO COMMIT / NEEDS FIXES]
```

---

## Workflow Integration

### Before Every Commit
1. Run Quick Verification (3 commands)
2. Fix any issues found
3. Re-run verification to confirm fixes
4. Only commit when all checks pass

### After Fixing Pre-commit Hook Failures
1. Run the check that failed (pytest or ruff)
2. Verify the fix worked
3. Run full Quick Verification
4. Commit with new changes (don't amend)

### For Pull Requests
1. Run Full Verification
2. Include verification summary in PR description
3. Test locally if possible

---

## Performance Tips

- **Quick feedback**: Run healthcheck first before full test suite
- **Parallel checks**: Can run ruff checks while tests are running
- **Focused testing**: Use `pytest tests/test_<specific>.py` for targeted verification
- **Watch mode**: Use `pytest --watch` during active development

---

## Notes

- This skill performs read-only operations (no code modification without explicit commands)
- Aligns with verification workflow documented in CLAUDE.md
- Can be invoked manually with `/verify-changes` or automatically before commits
- Always verify changes before committing to maintain code quality
