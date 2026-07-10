# Testbench plan — bulletproofing the monitor before production

Goal: wide coverage of true triggers and false positives for all 11 sources and
all 3 detection styles, exercised end-to-end on GitHub Actions (detect → gate →
enrich (LLM) → digest → email → commit), using **real TanStack release history**
via watermark rewinds. Iterate on any incorrect behaviour, keep the production
agent simple.

## Method

**Watermark rewinds, not sim repos.** Setting a source's `last_seen_id` in
`state.json` below a past release makes the next run re-discover the real
releases above it — testing the parsers against genuine TanStack bodies (real
prereleases, real framework adapter packages, real dual tags, real dep-only
bullets). One throwaway sim repo is used only for the three things history
cannot provide: yank detection, malformed-body fallback, and live settle-delay
(optional Phase 4).

**Isolation: the `testbench` branch.** GitHub fires cron only on the default
branch, so the branch is dispatch-only by construction; production keeps
running on `main` untouched. Nothing to revert at the end — delete the branch.

Branch deltas vs main (only these):
- `.github/workflows/monitor.yml`: remove `schedule:` block; concurrency group
  → `release-monitor-testbench`; **remove `RESEND_API_KEY` / `DIGEST_EMAIL_TO`
  env from the email step** (send.py then skips cleanly and logs
  "email not configured"); may add `BATCH_SETTLE_MIN: "1"` to the detect step
  if a settle test is wanted.
- `state.json`: edited per phase (the rewinds).
- `TESTBENCH-PLAN.md` results log updated as phases complete.
- `config.json` is NOT changed — the branch monitors the real TanStack repos.

Run a phase:
```sh
# on the testbench worktree: edit state.json (snippet below), commit, push, then
gh workflow run release-monitor --repo shivss26/tanstack-release-monitor --ref testbench
gh run watch <id> --repo shivss26/tanstack-release-monitor --exit-status
git pull   # run commits its artifacts to the branch; verify them
```

State-edit snippet:
```python
import json
s = json.load(open("state.json"))
s["<label>"]["last_seen_id"] = <rewind_id>   # see table below
open("state.json","w").write(json.dumps(s, indent=2) + "\n")
```

## Rewind table (real release ids, verified 2026-07-10/11)

| Source | Rewind `last_seen_id` to | Re-detects (true trigger) | Real false positives inside the window |
|---|---|---|---|
| router | just below `release-2026-07-01-2138` (id 347729321 − 1 = **347729320**) | the 2026-07-01 stable rollup | real **prerelease** rollups above it (2026-07-03-0048) are scanned and must be skipped |
| query | just below `release-2026-06-27-2033` (id 345780554 − 1 = **345780553**) | the 2026-06-27 stable rollup | real prerelease rollup 2026-06-26-1226 nearby; framework-scoped changes in body → noise |
| create-tsrouter-app | just below the newest rollup (id 343195141 − 1 = **343195140**) | the 2026-06-22 rollup — **body is "No changelog entries"** | expect: recorded, `reportable=False`, NO digest section (empty-rollup path) |
| table | **212310580** | `v8.21.3` (rollup body, `vX.Y.Z` tag) | ~40 real `v9.0.0-beta.N` prereleases above it must be skipped; `### Docs` category → noise; `@tanstack/angular-table@8.21.4` doesn't match the tag regex |
| intent | **348213168** | `v0.3.5` (single-package, markdown PR links → PR fetch) | duplicate release `@tanstack/intent@0.3.5` (id 348700319) must be ignored |
| form | **330997111** | the 2026-07-09 batch: 8 React-relevant releases | 7 framework packages (vue/svelte/solid/preact/lit/angular) → `excluded_tags`; duplicated bullets dedupe to ONE per PR; `Updated dependencies` → noise |
| db | look up: id just below `@tanstack/db@0.6.14`'s batch (2026-07-02T14:05Z) | the 2026-07-02 batch (~20 releases, core-only substantive) + `query-db-collection@1.0.47` (Jul 8) | vue/svelte/solid/angular-db excluded; adapters dep-only → noise; persistence adapters (expo/tauri/…) INCLUDED by design |
| ai | **349713000** | the 2026-07-06 batch (31 in / 9 out) | **suffix/mid-token frameworks**: `ai-vue`, `ai-solid-ui`, `solid-ai-devtools`, `preact-ai-devtools` must be excluded; `ai-react`, `react-ai-devtools`, providers/sandboxes included |
| virtual | **345264699** | the 2026-06-30 batch (core substantive; react dep-only) | `@tanstack/marko-virtual@3.14.0` (Jul 1, **substantive but marko-only**) and `solid-virtual@3.13.32` (Jul 2) must be excluded entirely |
| store | two-step: (a) rewind to just below `lit-store@0.14.0` (id 341488927 − 1 = **341488926**) | — | (a) expect "non-React release(s) skipped, **nothing emitted**" — the framework-only-batch path |
| store (b) | look up: id just below the 2026-04-17 batch (`store@0.11.0`, 15:00Z) | the Apr 17 batch (core + react-store, duplicated bullets) | grouped with later lit/preact FP releases into one batch — those land in `excluded_tags` |
| pacer | look up: id just below the 2026-05-14 batch (02:15Z) | the May 14 batch (pacer, pacer-lite, pacer-devtools, react-pacer(+devtools) in) | solid/preact/angular pacer packages excluded; bullets duplicated across adapters dedupe to one |

Ids marked "look up" — fetch with:
`gh api 'repos/TanStack/<repo>/releases?per_page=100' --jq '.[] | [.id, .tag_name, .published_at] | @tsv'`

Note: re-detection is idempotent-safe — artifacts are keyed by detection stamp,
so re-running a window in a later phase just writes new files; nothing collides.

## Phases

**P0 — branch smoke.** Create branch + edits, dispatch with NO rewinds.
Expect: all sources "no new releases", pending=0, no enrich, email step logs
"email not configured", commit only if state pruning changed anything.

**P1 — individual sources (11 runs, one source at a time).** For each row of
the rewind table: rewind → dispatch → verify:
1. Detect log matches the expected trigger + skip lines exactly.
2. Raw record correct (style field; batch `releases`/`excluded_tags` exact).
3. Summary bullets: substantive changes present, ONE bullet per underlying
   change, no framework-only change surfaces, breaking changes flagged,
   plain language, no API identifiers.
4. Noise lines correct (dep bullets, docs/chore, framework counts).
5. Digest written with exactly the reportable sections; sent/ marker ABSENT
   and send.py logged the skip.
6. Non-reportable cases (cta empty rollup, store framework-only) produce NO
   digest.
Fix-and-iterate: any failure → fix on main → merge main into testbench →
re-run that source before moving on. Log results below.

**P2 — combo publishes (append behaviour).** Three waves, fresh rewinds in one
commit each:
- Wave A: table + intent + form → one digest, 3 sections.
- Wave B: db + ai + virtual → one digest, 3 sections.
- Wave C: query + store(b) + pacer → one digest, 3 sections.
Verify section ordering/formatting, one email-skip log, combined digest reads
cleanly.

**P3 — maximal (all 11 at once) + the one real email.**
1. Mark all testbench digests as sent WITHOUT emailing: for every
   `digest/*.md` on the branch lacking a `sent/` marker, write
   `sent/<name>` containing `sent id=testbench-suppressed`.
2. Restore `RESEND_API_KEY` / `DIGEST_EMAIL_TO` env to the branch workflow.
3. Rewind ALL 11 sources in one commit → dispatch.
Expect: one run detects everything, ~10 reportable sections (cta likely
empty-rollup again), ONE email delivered, formatting of a large digest holds
up. This is the only email the whole testbench sends.

**P4 (optional) — single sim repo for the three untestable-from-history paths.**
One public repo `shivss26/tanstack-monitor-sim` added temporarily to the
branch config as a rollup source:
- Yank: publish a rollup release, let a run record it, delete the release,
  next run must write `yanks/` + a yank notice + digest "Retracted" section.
- Malformed body: publish a rollup whose body has no `## Changes` →
  format_matched=False path → summary carries the UNRECOGNISED FORMAT warning.
- Settle (batch style, `BATCH_SETTLE_MIN: "1"`): switch the sim source to
  package-batch, publish `@tanstack/sim@1.0.0`-style releases, dispatch within
  the settle window → "still settling; deferred", dispatch after → detected.
Then remove the sim source from branch config. Repo deleted (or kept) per
owner's call — deleting needs the `delete_repo` gh scope.

**Teardown.**
- Confirm `main` untouched except deliberate fixes merged during iteration.
- Delete the `testbench` branch and worktree.
- Confirm production cron (`17 */4 * * *`) + email env intact on main, and
  production `state.json` still at true heads (it never left main).
- Fold any lessons into TESTING.md; update the results log here and either
  commit this plan to main (as a record) or drop it.

## Production-readiness checklist (exit criteria)

- [ ] All 11 sources trigger correctly in isolation (P1).
- [ ] All false-positive classes verified: prerelease rollups, beta tags,
      duplicate tags, framework packages (prefix AND suffix/mid-token),
      framework-only publishes, dep-only bullets, docs/chore categories,
      empty rollups.
- [ ] Dedupe: one bullet per underlying change across packages.
- [ ] Append: multi-source digests compose correctly (P2).
- [ ] Maximal digest + real email formatting verified (P3).
- [ ] No unintended emails during P0–P2 (send.py skip logged every run).
- [ ] Failures encountered were fixed on main and re-verified.
- [ ] Branch deleted; production config/schedule/email confirmed intact.

## Results log

| Phase | Source(s) | Run | Outcome | Notes |
|---|---|---|---|---|
| (append as executed) | | | | |
