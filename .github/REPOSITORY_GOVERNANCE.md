# Repository Governance — Git & Branch (Phase 1)

`main` is the protected source of truth. Normal flow:

```
developer → feature branch → Pull Request → required validation → review → merge → main
```

Direct pushes to `main` are not the normal path. No history rewrites, no force-pushes, no PRs merged with red CI.

Existing public `main` history (including merge commits and short
`fix:` subjects from earlier phases) is preserved as engineering
evidence — it is never rewritten for appearance. Cleanliness applies
forward: every new milestone reaches `main` as one conventional,
squashed PR commit.

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
- One PR = one logical objective. A PR title answers "what meaningful
  change reaches `main`?" using the commit convention below
  (e.g. `feat: improve Strategy Lab universe selection`).
- Feature branches may be messy during development (`fix`, `wip`,
  `test`, `polish` commits are fine there) — the PR is squashed on
  merge, so branch journals never reach `main`.
- PRs must be mergeable (no conflicts) before merge.
- Conversation threads should be resolved before merge.
- The PR template checklist (testing, conventions, memory update) must be
  honestly complete — never ticked blindly.

## Commit convention (subjects on `main`)

Conventional commits; one commit = one logical change
(`AGENTS.md` §Commit Style is the working rule, extended here):

- `feat:` user-visible feature or capability
- `fix:` bug fix
- `perf:` performance improvement with measured evidence
- `refactor:` behavior-preserving restructure
- `test:` tests only
- `docs:` documentation only
- `build:` build system / packaging
- `ci:` CI workflows and validation
- `chore:` routine maintenance (graph regen, housekeeping)

Rejected as `main` subjects: `fix`, `update`, `changes`, `test`,
`final`, `snapshot`, `wip` and bare verbs with no object — they
describe keystrokes, not milestones. They remain acceptable inside
unmerged feature branches.

## Local guardrail (opt-in)

No server-side subject lint exists on this plan. To catch generic
subjects before push, install the local hook (machine-only, never
committed as enforcement):

```powershell
# .git/hooks/commit-msg  (chmod +x on Unix; exact filename, no extension)
$text = Get-Content $args[0] -Raw
if ($text -match '^(fix|update|changes|test|final|snapshot|wip)\s*$') {
  Write-Error "Generic subject rejected by repository policy (see .github/REPOSITORY_GOVERNANCE.md)."
  exit 1
}
if ($text -notmatch '^(feat|fix|perf|refactor|test|docs|build|ci|chore)(\(.+\))?: .+') {
  Write-Error "Subject must be '<type>: <object>' (see .github/REPOSITORY_GOVERNANCE.md)."
  exit 1
}
```

The hook is advisory process, not CI: squash-merge titles are set in
the GitHub UI at merge time, where the PR title (already conventional)
is reused verbatim.

## Required checks

- Required CI check name: **`CI gate`** (job `gate` in
  `.github/workflows/ci.yml`; it passes only when `quality`, `validators`
  and `build-test` all pass).
- Do not merge when `CI gate` (or any of its four jobs) is failing. Cannot
  be server-enforced on this plan — see "Manual steps".

## Merge strategy

- Preferred method for feature/fix/perf PRs: **squash merge** — the PR
  becomes exactly one logical commit on `main`, whatever the branch
  history looked like during development.
- Merge commits remain allowed for exceptional cases only (e.g. release
  trains joining long-lived lines); they must not be the normal path for
  feature work. Rebase-merge is discouraged for the same reason: `main`
  should read as logical milestones, not workstation journals.
- Server toggles (`allow_squash_merge`, `allow_merge_commit`,
  `allow_rebase_merge`) are all ON and stay ON: with a single maintainer
  and no server-side protection on this plan, the preference is enforced
  by this policy + review, never by disabling methods outright.
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
