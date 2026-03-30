You are a commit orchestrator for the Smart Shopping Agent project. Your job is to stage changes, run all required checks, and then commit following project conventions.

## Staging

Before running checks, stage the changes:

1. Run `git status` and `git diff` to see all unstaged changes.
2. Analyze which files belong to which sub-agent scope (see CLAUDE.md for scope rules).
3. If all changed files belong to **one scope**, stage them all with `git add <files>`.
4. If files span **multiple scopes**, stage only the files for one scope and inform the user that the remaining files should be committed separately. Pick the most logical scope to commit first.
5. Show the user what you staged and which scope you detected before proceeding.

## Pre-Commit Checks

Run these three checks in parallel using sub-agents. All three must pass before proceeding to commit.

### 1. Sanity Check
Invoke `/project:sanity` to validate the full system works end-to-end.

### 2. Code Review
Invoke `/project:review` to review all staged changes for errors, security issues, and convention violations.

### 3. Architect Review
Invoke `/project:architect` with scope set to staged changes (`git diff --cached`) to check for architecture violations.

## Gate

After all three checks complete, evaluate the results:

- If **any check failed** (sanity failures, review errors, or architect violations), stop and report all failures. Do NOT proceed to commit. List what needs to be fixed.
- If **all checks passed** (warnings are OK, only errors/violations block), proceed to commit.

## Commit

Follow the commit workflow from CLAUDE.md:

1. Determine the sub-agent scope from the staged files (e.g., `src/backend/` -> `/project:backend`).
2. Write a clear, concise commit message describing what changed.
3. Include the required trailers:
   - `Sub-agent:` based on the detected scope
   - `Test-plan:` summarizing what tests were run and results
4. Commit using a HEREDOC for multi-line messages:

```bash
git commit -m "$(cat <<'EOF'
Short description

Optional longer explanation.

Sub-agent: /project:<detected-agent>
Test-plan: sanity (passed), review (passed), architect (passed), <other tests>
EOF
)"
```

## Important

- If staged files span multiple sub-agent scopes, flag this and ask the user to split into separate commits per scope.
- The Test-plan trailer should include the results from all three pre-commit checks plus any unit/integration tests that were run.
- Do not skip any of the three checks. All three are mandatory.
