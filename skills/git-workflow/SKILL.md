---
name: git-workflow
description: Manage workspace version control with git for all files.
---

# Git Workflow

Use git for version control, rollback safety, and collaboration across all workspace files.

## Anti-Loop Guardrails
- Max 6 tool steps per task.
- Same call repeats twice → stop: "Loop detected — stopping."
- Max 2 retries → ask for direction.
- Simple query → answer in 1 step.

## Execution Guardrails
- Limit to 3 uses per task.
- No new info → stop and summarize.
- After 2+ calls → condense to 1 bullet.

## Quick Start

```bash
# Check current status
git status

# See recent commits
git log --oneline -10

# Pull latest changes
git pull origin main

# Stage, commit, push
git add .
git commit -m "description"
git push origin main
```

## Core Workflow: Protecting Important Documents

**Applies to:** processes/, outputs/, research/, skills/, MEMORY.md, AGENTS.md — any file with hours of work.

### Before Updating ANY Important Document

**Step 1: Commit current version as safety checkpoint**
```bash
# Stage the file (or all files in directory)
git add processes/process-track-meet-travel.md

# Commit with "before" message
git commit -m "checkpoint(process): save current before updating track-meet-travel"

# Push to preserve
git push origin main
```

**Step 2: Now make your changes**
- Edit the document
- Test/validate changes

**Step 3: If changes are good, commit them**
```bash
git add processes/process-track-meet-travel.md
git commit -m "feat(process): update track-meet-travel with Phase 2 details"
git push origin main
```

**Step 4: If changes go wrong, REVERT immediately**
```bash
# See the checkpoint commit
git log --oneline -5

# Revert to the checkpoint (restores file to pre-change state)
git checkout HEAD~1 -- processes/process-track-meet-travel.md

# Commit the restoration
git commit -m "revert(process): restore track-meet-travel to checkpoint"
git push origin main
```

### Safety Checkpoint Pattern

| Step | Action | Command |
|------|--------|---------|
| 1 | Commit current as checkpoint | `git add file && git commit -m "checkpoint: save before changes"` |
| 2 | Make edits | Edit file normally |
| 3a | Success: Commit changes | `git add file && git commit -m "feat: description"` |
| 3b | Failure: Revert to checkpoint | `git checkout HEAD~1 -- file && git commit -m "revert: restore checkpoint"` |

**This protects hours of work.** If the update fails, the original is always recoverable from the checkpoint commit.

## Core Workflow: Before Starting Work

Always begin from known good state:

```bash
# 1. Check status
git status

# 2. Check if behind remote (for awareness only, never auto-pull)
git fetch --dry-run 2>&1 || git status

# 3. Review recent changes
git log --oneline -5
```

**Clean working state required.** If uncommitted changes exist:
- Commit them first, OR
- Stash: `git stash` (save for later), OR
- Discard: `git checkout -- .` (lose changes — confirm with user first)

## Core Workflow: Making Changes

### 1. Small, Frequent Commits — No Exceptions

**Critical rule: Never leave uncommitted changes sitting between logical units.** Every time you complete a reviewed, working unit of work, commit it immediately. Uncommitted changes are not safe — they cannot be rolled back.

| When to Commit | Example Message |
|----------------|-----------------|
| After creating skill | `feat(skill): add cron-job-creator with validation scripts` |
| After fixing bug | `fix(skill): resolve validation error in family-travel-track` |
| After optimizing | `refactor(skill): reduce token usage, move details to references/` |
| After ruff --fix auto-fixes | `chore: ruff --fix auto-fixes (unused imports, f-strings)` |
| After manual fixes to remaining errors | `fix: remove unused locals (pipeline_g, test_pipeline_g)` |
| After updating docs | `docs(process): add git workflow section to skills-policy` |
| Daily sync | `chore: daily workspace sync 2026-02-21` |

**Anti-pattern — never do this:**
```
- Make change A
- Make change B
- Make change C
- "I'll commit at the end of the session"
```
If the system crashes after B but before C, both are lost. Commit after each one.

**Batch related changes into one commit; separate unrelated changes:**
```bash
# Good: auto-fixes in one commit, manual fixes in another
git add shared/api_server.py shared/embedding_service.py
git commit -m "chore: ruff --fix auto-fixes (unused imports, f-strings)"

git add pipeline_g/pipeline_g.py tests/test_pipeline_g.py
git commit -m "fix: remove unused locals (pipeline_g check_eligible, test_pipeline_g)"
```

**Remaining errors after auto-fix: review and commit by category:**
```bash
# Good: each category of manual fix gets its own commit
git add shared/verify_ingest.py
git commit -m "fix: resolve bare except in verify_ingest"

git add shared/batch_ingest_branch.py
git commit -m "fix: resolve undefined V2_REGISTRY_PATH in batch_ingest_branch"
```

### 2. Commit Scope

**Single skill/feature per commit:**
```bash
git add skills/cron-job-creator/
git commit -m "feat(skill): add cron-job-creator"

git add processes/process-skills-policy.md
git commit -m "docs(process): update with token efficiency guidelines"
```

**NOT:** One giant commit with 10 unrelated changes.

### 3. Commit Messages

**Format:** `type(scope): description`

| Type | Use For |
|------|---------|
| `feat` | New feature, skill, capability |
| `fix` | Bug fix, error correction |
| `refactor` | Restructuring, optimization |
| `docs` | Documentation updates |
| `chore` | Maintenance, cleanup, sync |
| `revert` | Rolling back previous change |

**Good examples:**
- `feat(skill): add git-workflow skill for version control`
- `fix(cron): update morning briefing data source`
- `refactor(skills): optimize family-travel-track, 60% token reduction`
- `docs(memory): add token efficiency lessons from skill building`
- `chore: daily workspace sync 2026-02-21`

## Core Workflow: Rollback & Recovery

### When Change Goes Wrong

**Option 1: Revert last commit (keeps history)**
```bash
# See what changed
git log --oneline -3
git show HEAD

# Revert (creates new commit undoing changes)
git revert HEAD

# Push reversion
git push origin main
```

**Option 2: Reset to previous commit (destroys history — use carefully)**
```bash
# Reset to specific commit
git reset --hard abc1234

# Force push (DANGEROUS — only if sure)
git push origin main --force
```

**Option 3: Restore specific file from history**
```bash
# Get file from last commit
git checkout HEAD -- path/to/file

# Get file from 3 commits ago
git checkout HEAD~3 -- path/to/file

# Get file from specific commit
git checkout abc1234 -- path/to/file
```

### Finding the Right Commit to Revert

```bash
# See all commits
git log --oneline -20

# See what changed in specific commit
git show abc1234

# See commits affecting specific file
git log --oneline -- path/to/file
```

## Core Workflow: Collaboration Safety

### Before Major Changes

```bash
# Create backup branch (optional safety)
git branch backup-before-refactor

# Make changes...

# If changes fail, restore from backup
git checkout backup-before-refactor
```

### Working Across Multiple Sessions

```bash
# End of session: commit everything
git add .
git commit -m "wip: end of session, skill building in progress"
git push origin main

# Next session: pull latest
git pull origin main
```

## Git Commands Quick Reference

| Task | Command |
|------|---------|
| Check status | `git status` |
| See commits | `git log --oneline -10` |
| Pull latest | `git pull origin main` |
| Stage all | `git add .` |
| Stage specific | `git add path/to/file` |
| Commit | `git commit -m "message"` |
| Push | `git push origin main` |
| Revert last | `git revert HEAD` |
| Restore file | `git checkout HEAD -- path/to/file` |
| See changes | `git diff` |
| Stash wip | `git stash` |
| Restore stash | `git stash pop` |

## Integration with Other Skills

### Before Using skill-creator

```bash
git status  # ensure clean
git pull origin main  # get latest
# Create or improve skill
# Test and validate
# Then commit...
```

### Before Using cron-job-creator

```bash
git status
# Create cron job
# Test it works
# Then commit job definition...
```

### After Any Significant Change

```bash
git add affected/files
git commit -m "type(scope): description"
git push origin main
```

## ⚠️ Critical Pitfall: `git checkout -- file` vs Staging Area State

**`git checkout -- <file>` restores from the *index* (staging area), NOT from HEAD.**

If the staging area has a corrupted, truncated, or partially-staged version of a file — and you run `git checkout -- <file>` — it will overwrite your working tree with whatever is in the index, destroying your good working-tree copy.

**This session's incident (2026-05-26):**
- `zk-rag-operations/SKILL.md` was accidentally staged with a 20-line truncated version during a complex multi-file staging operation
- `git checkout -- skills/zk-rag-operations/SKILL.md` was run to "restore" it
- Result: the 2017-line good version in the working tree was overwritten by the 20-line staged version
- Recovery: `git show HEAD:skills/zk-rag-operations/SKILL.md > path/to/file` (restore directly from the commit, bypassing the index entirely)

**Safe pattern — always verify before checkout:**
```bash
# Check what's in the staging area vs HEAD before restoring
git show :skills/zk-rag-operations/SKILL.md | wc -l   # staging area version
git show HEAD:skills/zk-rag-operations/SKILL.md | wc -l  # committed version

# If they differ and you want HEAD's version:
git show HEAD:path/to/file > path/to/file   # bypasses staging area
git restore --source=HEAD path/to/file      # alternative (git 2.23+)
```

**Prevention:** When unstaging or resetting a file, use `git reset HEAD <file>` (unstages without touching working tree), then `git checkout HEAD -- <file>` only after verifying the index is clean.

## Safety Rules

1. **Never force push to main** unless explicitly instructed
2. **Always check status before starting** — avoid conflicts
3. **Commit before risky operations** — easy rollback point
4. **Use descriptive messages** — future you will thank you
5. **Push regularly** — backup to GitHub

## When NOT to Use Git

- Temporary test files (don't commit, add to .gitignore)
- Large binary files (use .gitignore, store elsewhere)
- Sensitive credentials (never commit, use .env or auth system)
- Generated/cache files (add to .gitignore)

### When `gh` is unavailable for PR creation

`gh` is not installed on the R730 (Fred). If `gh auth status` shows "not logged in" and no token is found in `~/.hermes/.env`, fall back to:

1. Push the branch: `git push -u origin HEAD`
2. Provide the PR URL: `https://github.com/blockops1/zk-rag-system/pull/new/<branch>`
3. Mr. V pastes the body manually, or paste the PR description into Telegram for him to copy

**Do not wait on auth setup** — the branch is pushed, the code is reviewable, the PR URL is ready.

### ⚠️ Never Combine Stash with Branch Switching

**Anti-pattern — causes silent working-tree corruption:**
```bash
git stash push -m "wip" && git checkout main && git merge feature-branch && git stash pop
```
This fails because: `git stash push` stages changes to the stash but the working tree remains on the current branch. `git checkout main` moves to main (dropping uncommitted changes). `git merge` consumes the stash (applies it to main). By this point the working tree is already on main with main's files — the merge did NOT bring the stashed changes because they were committed to the merge. `git stash pop` then restores whatever was stashed before the push (which may be nothing or a stale version).

**Recovery:** Copy file directly from branch ref: `git show <branch>:<path> > <dest>`
**Prevention:** Always commit before switching branches. If you must stash temporarily, use `git stash` (without push) after confirming `git status` is clean, or use `git restore <path>` to explicitly restore from a specific ref.

## RAG System Repo
- URL: git@github.com:blockops1/zk-rag-system.git
- Branch: main (always pull before editing)
- Working copy on server: /tmp/document_rag_system/ (synced from live scripts in <VENV>scripts/)
- Live scripts: <VENV>scripts/
- Project plan: /data/rag/mil-docs-staging/PROJ-rag-pipeline-restructuring.md
- Large data dirs (.gitignore): uploads/, qdrant_data/, ingested/, images/, chunks/, embeddings/, /data/rag/*staging/

## References

- Git official docs: https://git-scm.com/doc
- GitHub guides: https://docs.github.com/en/get-started
- Workspace skills: `skills/*/old/` for archived versions (if manual backup needed)
