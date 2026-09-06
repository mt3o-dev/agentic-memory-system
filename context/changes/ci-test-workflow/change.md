---
change_id: ci-test-workflow
title: Run the test suite in CI on every push and pull request
status: implemented
created: 2026-09-06
updated: 2026-09-06
archived_at: null
memory_goal: 29b6c0dc-7cab-4ab9-a2d5-50e6904181eb
---

## Notes

Closes the issue the backfill recorded: `.github/workflows/` held only `release.yml`,
which triggers on version tags and never calls pytest, so 337 tests ran on contributors'
machines and nowhere else and a green PR meant only that whoever opened it remembered.

**The runner.** The intent was the org's Ampere (Oracle ARM64) self-hosted runner. It
cannot serve this repository, and the reason is worth keeping: GitHub refuses to dispatch
jobs from a **public** repository to a self-hosted runner whose group has
`allows_public_repositories: false` — the default, and a deliberate one, because a fork's
pull request would otherwise execute arbitrary code on the maintainer's own hardware.
Confirmed empirically rather than inferred: with the runner online, idle and correctly
labelled, the job sat queued indefinitely.

Relaxing that policy for a test suite was judged not worth it, so CI runs on
GitHub-hosted `ubuntu-24.04-arm` — same architecture the Ampere box would have given,
free for public repositories, suite green in about ten seconds.

**Consequences of that choice, both ways round.** Fork pull requests are now *tested*
rather than skipped: the fork-skip guard is mandatory on self-hosted hardware and
pointless on an ephemeral GitHub-hosted one. And the actions had to move off the
deprecated Node 20 runtime, which cost one red build — `astral-sh/setup-uv` publishes
moving major tags only up to v7 while its releases have reached v10.x, so `@v10` does not
resolve at all and the job died before checkout.

## Left open

`release.yml` still pins the Node 20-era actions and will break when Node 20 is removed
from the runners. Deliberately untouched — a release pipeline is not this change's
business — and recorded as an issue in the graph so it is a dated liability rather than a
surprise.
