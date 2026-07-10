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
| db | **346994131** (`@tanstack/vue-db@0.0.124`, 2026-06-30T17:28Z — just below the Jul-2 batch) | the 2026-07-02 batch (~20 releases, core-only substantive) + `query-db-collection@1.0.47` (Jul 8) | vue/svelte/solid/angular-db excluded; adapters dep-only → noise; persistence adapters (expo/tauri/…) INCLUDED by design |
| ai | **349713000** | the 2026-07-06 batch (31 in / 9 out) | **suffix/mid-token frameworks**: `ai-vue`, `ai-solid-ui`, `solid-ai-devtools`, `preact-ai-devtools` must be excluded; `ai-react`, `react-ai-devtools`, providers/sandboxes included |
| virtual | **345264699** | the 2026-06-30 batch (core substantive; react dep-only) | `@tanstack/marko-virtual@3.14.0` (Jul 1, **substantive but marko-only**) and `solid-virtual@3.13.32` (Jul 2) must be excluded entirely |
| store | two-step: (a) rewind to just below `lit-store@0.14.0` (id 341488927 − 1 = **341488926**) | — | (a) expect "non-React release(s) skipped, **nothing emitted**" — the framework-only-batch path |
| store (b) | **307364164** (`@tanstack/vue-store@0.10.0`, 2026-04-10 — just below the Apr-17 batch) | the Apr 17 batch (core + react-store, duplicated bullets) | grouped with later lit/preact FP releases into one batch — those land in `excluded_tags` |
| pacer | **310540474** (`@tanstack/solid-pacer@0.21.0`, 2026-04-17 — just below the May-14 batch; production watermark is the top of that batch, so exactly one batch re-detects) | the May 14 batch (pacer, pacer-lite, pacer-devtools, react-pacer(+devtools) in) | solid/preact/angular pacer packages excluded; bullets duplicated across adapters dedupe to one |

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

- [x] All 11 sources trigger correctly in isolation (P1).
- [x] All false-positive classes verified: prerelease rollups, beta tags,
      duplicate tags, framework packages (prefix AND suffix/mid-token),
      framework-only publishes, dep-only bullets, docs/chore categories,
      empty rollups.
- [x] Dedupe: one bullet per underlying change across packages.
- [x] Append: multi-source digests compose correctly (P2).
- [x] Maximal digest + real email formatting verified (P3).
- [x] No unintended emails during P0–P2 (send.py skip logged every run).
- [x] Failures encountered were fixed on main and re-verified. (None encountered: 15/15 runs passed first time.)
- [x] Branch deleted; production config/schedule/email confirmed intact.

## Results log

| Phase | Source(s) | Run | Outcome | Notes |
|---|---|---|---|---|
| P0 | all (no rewinds) | 29117162934 | PASS | 11× "no new releases"; enrich skipped; "no unsent digests"; no commit |
| P1 | router | 29117305522 | PASS | stable rollup re-detected, prerelease above skipped (watermark back at 347729321); 2 substantive bullets (PRs 7695/7662), 2 noise; 1 iteration, 0 web calls; digest written; email skip logged |
| P1 | query | 29117476474 | PASS | 2026-06-27 stable rollup re-detected, 06-26 prerelease not picked up; 6 substantive fix bullets + 2 noise; digest written; 2 digests now unsent; email skip logged |
| P1 | create-tsrouter-app | 29117618803 | PASS | empty rollup ("No changelog entries"): recorded, deterministic note, reportable=False, NO LLM call (23s run), NO digest, "nothing reportable -> NO EMAIL" |
| P1 | table | 29117761468 | PASS | only v8.21.3 detected — all v9 betas + angular-table tag skipped; ### Docs (incl. loose-parsed vue-example line) → noise, only #5989 fetched; 1 bullet + 2 noise; published_at fallback shows true 2025-04-15 date |
| P1 | intent | 29117919474 | PASS | v0.3.5 detected once; duplicate @tanstack/intent@0.3.5 tag ignored; 2 bullets, breaking change flagged; single-package PR fetch worked |
| P1 | form | 29118084673 | PASS | batch-2026-07-09-1049: 8 in / 7 framework excluded (exact); 8 deduped bullets (one per change); both noise lines ("Plus 7 …" + "7 non-React … filtered out") |
| P1 | db | 29118232655 | PASS | Jul 2–8 window merged into one batch (20 in / 5 framework excluded); 3 substantive core bullets + 18 noise; persistence adapters included by design; multi-cluster merge only possible under rewind (prod window ≤ 4h) |
| P1 | ai | 29118387623 | PASS | 31 in / 9 out exact; all suffix/mid-token FPs excluded (ai-vue, ai-vue-ui, ai-solid-ui, solid-ai-devtools, preact-ai-devtools, …); ai-react/react-ai-devtools/providers kept; 2 substantive bullets + 30 noise |
| P1 | virtual | 29118581019 | PASS | batch-2026-06-30-1522: 2 in / 7 framework excluded (exact: marko-virtual@3.14.0, solid-virtual@3.13.32, plus vue/lit/angular/svelte); framework-only packages correctly excluded from releases array and present only in excluded_tags; 2 substantive core bullets (scroll perf, viewport-drift fix) + noise lines; digest written; email skip logged
| P2 | table+intent+form (Wave A) | 29118726294 | PASS | 3 sections in one digest (2026-07-11-0109.md): table v8.21.3 (1 substantive + 2 noise), intent v0.3.5 (2 substantive with breaking change), form batch-2026-07-09-1049 (8 substantive + 8 noise/framework); email skip logged; 9 digests unsent total
| P2 | db+ai+virtual (Wave B) | 29118827660 | PASS | 3 sections in one digest (2026-07-11-0111.md): db batch-2026-07-08-0441 (3 substantive + 18 noise/framework), ai batch-2026-07-06-1739 (2 substantive + 30 noise/framework), virtual batch-2026-06-30-1522 (2 substantive + 7 framework/noise); email skip logged; 10 digests unsent total
| P2 | query+store+pacer (Wave C) | 29118935914 | PASS | 3 sections in one digest (2026-07-11-0113.md): query release-2026-06-27-2033 (6 substantive fix + 2 noise), store batch-2026-04-17-1500 (1 substantive + 7 framework/noise), pacer batch-2026-05-14-0215 (1 substantive + 15 framework/noise); email skip logged; 11 digests unsent total
| P1 | store (a) | 29119154908 | PASS | framework-only window (lit-store@0.14.0): "[store] 1 non-React release(s) skipped, nothing emitted"; NO raw record, NO enrich, NO digest; watermark still advanced (only state.json in bot commit) |
| P3 | ALL 11 (maximal) | 29119357576 | PASS | all 11 detected in one run; 10 reportable sections (cta empty-rollup non-reportable); one 248-line digest; exactly ONE email sent (Resend id 3aecb198-439b-4326-a5fa-dc580d679468); 11 suppressed digests untouched |

## Outcome (2026-07-11)

15/15 runs passed with zero fixes required — the production setup needed no
changes from the testbench. P4 (sim repo: yank / malformed-body / live settle)
was optional and skipped; those three paths remain covered only by code review.
Branch `testbench` deleted after this plan was copied to main as the record.
