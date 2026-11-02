# Main branch protection

This file is the prepared policy only. No repository branch-protection setting is changed by Phase 2e.

## Required configuration

Target branch: `main`

Enable:
- Require a pull request before merging.
- Require at least one approval (the repository owner may approve their own policy separately).
- Require status checks to pass before merging.
- Require conversation resolution.
- Block force pushes and branch deletion.

Required status check:
- `CI Required`

The `CI Required` job is the short, deterministic gate for both engines, core unit tests, randomized ring-buffer stress, dispatcher behavior, Hev linkage, and doctor diagnostics. The long Phase 2e measurement workflow is intentionally not a required check; it is an evidence/benchmark workflow and should not delay ordinary documentation or maintenance changes.

Do not select old phase-specific workflow names as required checks. They are historical, branch-scoped workflows and several are retained only as evidence of earlier phases.

## Apply after Phase 2e review

1. Open repository Settings → Branches → Add branch protection rule (or the repository's equivalent ruleset UI).
2. Set the branch pattern to `main`.
3. Enable the protections above.
4. Select the `CI Required` status check after it has appeared on a pull request.
5. Save the rule.

The rule is deliberately not activated by this change set.
