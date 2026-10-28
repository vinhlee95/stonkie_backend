---
name: review-map
description: Use when the user finds a PR or diff hard to review, asks for a visual map, diagram or walkthrough of a pull request, or wants to click through a large change step by step and ask questions about each part.
when_to_use:
  - The user says a PR is too big or hard to review
  - The user asks for a visual / interactive review aid for a PR
  - When requested by user with /review-map <PR number>
---

# Review Map

Turns one PR into a private claude.ai page: a diagram of the **runtime flow** the PR touches, where each box opens
that step's diff, tests, "what to check" list and an Ask Claude box scoped to that step.

The tool is mechanical (`build.py` + `template.html`). Your work is the **spec**: deciding the boxes and writing
accurate, verified notes for each. `example_spec.py` is a complete spec for PR #61. Read it before writing one.

## Workflow

Work in the scratchpad; nothing goes in the repo.

1. **Fetch** (`$S` = scratchpad dir, `N` = PR number):
   ```bash
   gh pr view N --json number,title,body,headRefName,headRefOid,files,additions,deletions
   gh pr diff N > $S/prN.diff
   git fetch origin <headRefName>
   python3 -I .claude/skills/review-map/build.py hunks --diff $S/prN.diff
   ```
2. **Read** the whole diff, then the surrounding code for every changed function (callers, what runs before/after).
   You can't draw the flow from the diff alone.
3. **Design the boxes.** Each box is a *step at runtime* (request comes in → X → Y → logged), not a file. Order them
   the way the code runs. Add dashed `context` boxes for unchanged steps between changed ones, so the reader sees
   where new code sits. Group a step's helpers as `children` of it. A separate entry point (e.g. a second handler)
   gets its own flow.
4. **Write the spec** to `$S/review_map/prN_spec.py` (format below). For each changed box:
   - `summary`: what the step does and what the PR changed, plain words, 2 to 4 sentences.
   - `checks`: what a reviewer should verify. Include the PR body's own review findings for that step, keeping their
     IDs (`FUNC-3: …`), plus what you found reading the code. **Verify every claim against the head code** (read the
     function, run a regex in Python) before writing it. Phrase uncertain ones as something to check.
   - `asks`: 1 to 2 questions the reviewer would likely type.
   - `code_refs` / `test_refs`: every hunk of the diff should land in some box. One hunk may sit in two boxes.
5. **Build and check:**
   ```bash
   python3 -I .claude/skills/review-map/build.py page --repo . --ref origin/<headRefName> \
     --diff $S/prN.diff --spec $S/review_map/prN_spec.py --out $S/review_map/prN-review-map.html
   ```
   Fix every error and `warning:` it prints (unknown ids, missing hunk, name not found, hunks no box shows; a small
   import-only hunk can be left out on purpose). Then syntax-check the page script: extract
   the last `<script>` to a `.js` file and run `node --check` on it.
6. **Publish** with the Artifact tool: `file_path` = the HTML, `icon: "code"`, `capabilities: {"sample": {}}` (the
   Ask box needs it), one-sentence `description`. The template already meets the artifact page contract, so skip
   the design pass. Republish to the same path after edits.
7. **Report**: the link, how to use it in two lines, and the findings you added beyond the PR body's.

## Spec format

A Python file defining `PR`, `NODES`, `FLOWS`, `TOUR`.

| `PR` key | |
|---|---|
| `number`, `title`, `url`, `branch`, `commit`, `additions`, `deletions`, `files` | required. `url`: Graphite link `https://app.graphite.com/github/pr/<owner>/<repo>/<n>` |
| `problem` | required. One or two sentences: what was wrong / what the PR is for |
| `summary_for_claude` | required. Dense PR summary (causes, fix, results, open findings) sent with every question |
| `results` | optional `[{label, before, after}]` metric tiles |
| `causes`, `causes_title` | optional `[{text, node}]`: each reason for the PR, linked to the box that fixes it |
| `asks` | optional overview-level suggested questions |

| `NODES[]` key | |
|---|---|
| `id`, `title`, `where` (function names), `tag` | `tag`: `new`, `changed`, or `context` (unchanged; needs only `summary`) |
| `note` | optional pill, e.g. `"stocks only"` |
| `summary`, `checks`, `asks` | see step 4. `` `backticks` `` render as code |
| `code_refs`, `test_refs` | lists of refs, below |

| Ref | Use for |
|---|---|
| `("hunk", path, [i, …], label)` | any change; indexes from `build.py hunks`. Several indexes join with a ⋯ gap |
| `("new", path, [names], label?)` | Python top-level defs/classes/constants **added whole** by the PR (all lines green) |
| `("ctx", path, [names], label?)` | unchanged Python defs worth showing |
| `("lines", path, start, end, label)` | unchanged lines in any language |
| `("lines", path, start, end, label, "+")` | part of a **new file** in any language (module header, imports, a TS function) |

`FLOWS`: `[{title, note?, frames: [{label, rows: [[id], [id, id]], parallel_row?, children?: {id: [ids]}}]}]`. A row
with two ids draws them side by side; `parallel_row: i` labels row `i` "in parallel". `TOUR`: changed node ids in
reading order (context boxes stay out).

## Common mistakes

- **One box per file.** Readers already have the file list. Boxes follow the execution path.
- **`new` on an edited function.** It would show unchanged lines as added. Use the function's `hunk`.
- **Unverified checks.** A wrong "bug" in the checklist costs the reviewer more than it saves. Read the code first.
- **Non-Python PRs:** `new`/`ctx` parse Python only. Use `hunk` and `lines`.
- **Skipping a new module's imports.** They show what the module depends on. Put them in the box that owns the module.
