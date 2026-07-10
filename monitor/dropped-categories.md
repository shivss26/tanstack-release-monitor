# Dropped categories — what prefetch treats as "noise"

This records what `prefetch.py` considers **noise**, so the behaviour stays
predictable and reviewable as new TanStack repos are added to the monitor.

## What "dropped" means here (important)

Noise is **only** skipped for **PR-body fetching** — we don't spend a GitHub API
call pulling the body of a change that won't describe a library behaviour change.

**Nothing is removed from the output.** The FULL rollup release notes are always
recorded verbatim, so every change — including every noise change — is still
visible to the enrichment agent. If the agent decides a "noise" change actually
matters, it can still see its line and look it up with the doc/web tools. So an
over-aggressive noise rule degrades gracefully (agent sees the title, just not the
pre-fetched body); it never hides anything.

## The rule (shared across ALL repos)

A change's PR body is **not** fetched if **any** of these hold
(`prefetch.is_noise`):

1. **Category `### Chore`.** Changesets emits the same category headers
   (`Fix` / `Features` / `Refactor` / `Performance` / `Chore`) in every TanStack
   repo. `### Chore` is dependency bumps (incl. `renovate[bot]` / `dependabot[bot]`),
   release tooling, nx/pnpm pins, changeset config, `.nvmrc`, tsconfig — never a
   library API/behaviour change.
2. **Infra scope** — the change's scope first-token is in `INFRA_SCOPES`:
   `examples, example, test, tests, benchmark, benchmarks, bench, e2e, ci, docs,
   doc, build, scripts, script`.
   These are monorepo infrastructure folders (demo apps, test/bench harnesses, CI,
   docs site, build config), present in every TanStack monorepo.
3. **Dotfile scope** — scope starts with `.` (e.g. `.nvmrc`). Belt-and-suspenders;
   these are almost always under `### Chore` anyway.

## Why this is the SAME for every repo (not per-repo)

- Mechanism 1 (`### Chore`) is a Changesets category — universal by construction.
- Mechanism 2 (infra scopes) are generic monorepo folders. Crucially, **none of
  the infra scopes collides with a TanStack library id**
  (`start, router, query, table, form, db, ai, intent, virtual, pacer, hotkeys,
  store, ranger, config, devtools, cli, workflow`). So a single shared list is safe
  for every repo — an entry that never occurs in a given repo simply never fires,
  and there is no scope that means "noise here but a real library there."

`deps` / `dependencies` / `release` are intentionally **not** in `INFRA_SCOPES`:
they always arrive under `### Chore`, so mechanism 1 already covers them.

## Verified against (2026-06, 30 rollups per repo)

| repo | noise observed |
|---|---|
| **query** | scopes `examples`, `examples/integrations`, `examples/nextjs-suspense-streaming`, `tests`, `build`; chores: renovate `deps:`, `.nvmrc`, `solid-query-devtools/tsconfig`, release-job, changeset generator, pnpm/chokidar pins |
| **router** | scope `benchmarks`, `examples`; chores: renovate `deps:`, nx updates, "forgot changeset" |

Drop-correctness was 100% across all 60 rollups in the verification audit — the
noise split has no false positives or negatives in the observed data.

## When adding a NEW TanStack repo

1. Run the test bench (`<repo>-test/`) over ~30 rollups.
2. Skim the change scopes. If a repo introduces a **new infra scope** not in the
   list above (e.g. a hypothetical `playground` or `fuzz` folder), add it to
   `INFRA_SCOPES` in `prefetch.py` **and** to the list here, with the date and repo.
3. Never add a token that is (or could become) a real TanStack library id — those
   must stay fetchable.

## React-stack filtering (added 2026-07)

The monitor covers a React tech stack only, so two more things count as noise:

1. **Other-framework scopes/packages** — `detect.OTHER_FRAMEWORKS`
   (vue, svelte, solid, preact, lit, angular, marko, qwik). In rollup bodies a
   change whose scope starts with one of these tokens is noise; in package-batch
   repos a package whose name contains one of these tokens (any position —
   `ai-vue`, `solid-ai-devtools`) is dropped from the batch entirely (still
   counted in the summary's "filtered out" line).
2. **Noise categories** — `### Chore` plus table's `### Docs` style headers
   (`prefetch.NOISE_CATEGORIES`).
3. **`Updated dependencies` changesets bullets** (package-batch repos) — version
   plumbing, never fetched, counted as noise.

### Change log
- 2026-06: initial set, derived from query + router (30 rollups each).
- 2026-07: added source `create-tsrouter-app` (the only other TanStack repo using
  the rollup tag format — the rest publish per-package `@tanstack/<pkg>@x.y.z`
  releases). Observed scope: `create` (substantive, NOT infra — no INFRA_SCOPES
  change needed). Some of its rollups are legitimately empty ("No changelog
  entries"), which the empty-sentinel path already handles.
- 2026-07: added React-stack filtering (frameworks + NOISE_CATEGORIES + dependency
  bullets) alongside the new table / intent / form / db / ai / virtual / store /
  pacer sources.
