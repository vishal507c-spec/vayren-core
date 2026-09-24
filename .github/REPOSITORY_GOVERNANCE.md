# Repository Governance — Git & Branch (Phase 1)

`main` is the protected source of truth. Normal flow:

```
developer → feature branch → Pull Request → required validation → review → merge → main
```

Direct pushes to `main` are not the normal path. No history rewrites, no force-pushes, no PRs merged with red CI.

## Protected branches

- `main` (default branch): protected **by policy**. Server-side branch
  protection / rulesets are unavailable on this plan (private repo, free
  tier — API returns 403), so until the plan changes, protection is
  enforced by process + review, not by GitHub settings. See "Manual steps".
- Feature branches (`fix/*`, `phase-*`, etc.): no restrictions; force-push
  there is allowed while iterating, but prefer clean commits.

## Pull requests

- Every change to `main` goes through a PR (see
  `.github/PULL_REQUEST_TEMPLATE.md`). Do not push directly to `main`.
- PRs must be mergeable (no conflicts) before merge.
- Conversation threads should be resolved before merge.

## Required checks

- Required CI check name: **`CI gate`** (job `gate` in
  `.github/workflows/ci.yml`; it passes only when `quality`, `validators`
  and `build-test` all pass).
- Do not merge when `CI gate` (or any of its four jobs) is failing. Cannot
  be server-enforced on this plan — see "Manual steps".

## Merge strategy

- Allowed methods (unchanged): merge commit, squash, rebase. History is
  linear conventional commits plus merge commits for PRs; keep it that way.
- `delete_branch_on_merge` is ON (merged heads auto-deleted).
- `allow_update_branch` is ON ("Update branch" button available).
- Never rewrite published history. Never rebase `main`.

## Force-push / deletion policy

- Force-push to `main`: forbidden. No technical block exists on this plan;
  treat any force-push to `main` as an incident (see recovery).
- Delete `main`: forbidden. Feature-branch deletion after merge is automatic.

## Tag policy

- Tags (`v*`) are release references: never delete, move, or overwrite them.
- No server-side tag protection exists on this plan; enforcement is by policy.

## Bypass policy

- No bypass actors are configured. The sole repository admin
  (`vishal507c-spec`) can push in an emergency, but must do so only to
  recover, never to skip validation, and must disclose it.
- Keep it that way: no broad bypass permissions.

## Emergency recovery

```powershell
# 1. Inspect damage (never on main first)
git fetch origin
git log origin/main --oneline -5
git status --short

# 2a. Bad commit merged to main -> revert (no rewrite)
git checkout main
git pull --ff-only origin main
git revert -m 1 <merge-sha>        # or: git revert <sha> for direct commits
git push origin main

# 2b. Branch deleted by accident -> recreate from reflog/remote SHA
git push origin <sha>:refs/heads/<branch>

# 2c. Bad tag moved -> restore from a trusted clone, then
git push origin :refs/tags/<tag>   # delete remote tag first (admin only)
git tag <tag> <good-sha>
git push origin tag <tag>
```

## Manual steps (need Pro plan or public repo)

`Settings → Branches → Add classic branch protection rule` for `main`:

- Require a pull request before merging (dismiss stale approvals on push).
- Require status checks to pass: check **`CI gate`** (detail jobs
  `quality`, `validators`, `build-test`); require branches up to date.
- Require conversation resolution before merging.
- Do not allow bypassing the above settings (no bypass list).
- Restrict deletions; block force pushes (both implied by the rule).
- Rulesets alternative: `Settings → Rules → New ruleset`, target `main`
  (+ `v*` tag ruleset: block deletion/updates), same required check `test`.

## Compatibility notes

- Workflow Actions permissions are read-only by default; full `CI` must stay
  green — protection only gates, it never replaces validation.
- No CODEOWNERS file: single-maintainer repo, no enforceable review gate on
  this plan; not creating fake reviewers.
- Commits are currently unsigned; signed commits are not required.
