# Testing & Verification

How to confirm the monitor is behaving correctly on recent releases. It covers two
things: **workflow correctness** (did the run perform the right steps and produce a
consistent set of files?) and **summary quality** (do the summaries faithfully and
clearly represent the raw releases?). Written so it can be followed cold — read
`README.md` first for how the pipeline fits together.

## The contract being verified

A single detection run produces, for each new release rollup it finds:

| Artifact | Path |
|---|---|
| Raw release record | `raw/<label>/<stamp>__<tag>.json` |
| Prefetched context | `prefetch/<label>/<stamp>__<tag>.md` |
| Summary | `summaries/<label>/<stamp>__<tag>.md` |
| Model transcript | `transcripts/<label>/<stamp>__<tag>.log` |
| Combined digest (if anything is reportable) | `digest/<run-stamp>.md` |
| Delivery marker (once emailed) | `sent/<run-stamp>.md` |

`<label>` is the source's config label, `<stamp>` is the detection time
(`YYYY-MM-DD-HHMM`), and `<tag>` is the release tag. **All artifacts for one rollup share
the same `<stamp>__<tag>`**, so they are findable together.

Two invariants follow from the design:
- **Decoupled and self-healing** — a missing summary (or delivery marker) for an existing
  input means that step is still pending and will be retried; an input is never
  reprocessed once its output exists.
- **Reportable vs not** — a rollup containing only chore/dependency/internal changes is
  recorded but produces no digest and no email.

## A. Workflow-correctness checks (mechanical)

Pick the most recent run, or a specific rollup, then:

1. **Find recent rollups** — list `raw/<label>/` for each configured source and take the
   newest few.
2. **Verify the file chain** — for each rollup, confirm every artifact in the table above
   exists and that the names share an identical `<stamp>__<tag>`.
3. **Check the summary header** — repository, library list, rollup-released time (IST,
   derived from the tag), detected time (IST), and release URL are all present and sane;
   the "Files" list at the bottom points at paths that actually exist.
4. **Idempotency** — exactly one summary per raw rollup (no duplicates), and a re-run
   produced no spurious new files (compare consecutive run commits).
5. **Delivery** — every digest has a matching `sent/` marker. A digest without one is an
   un-delivered digest that should send on the next run.
6. **Run log** — in the Actions run, confirm the stages ran in order (detect → pending
   gate → enrich only when there is pending work → email → commit) and that the enrich
   step reported its tool-call metrics.

## B. Summary-quality assessment (judgment)

This is the core performance check: the committed **summary vs the raw rollup**.

**Establish the source of truth** for a chosen rollup:
- the raw release notes (`raw/<label>/<stamp>__<tag>.json` → `body`), and
- the pull requests those notes reference — their descriptions are the ground truth for
  what changed. The prefetch file already gathers them; you may also read them directly on
  GitHub and consult the official TanStack documentation for any behaviour the PR text
  leaves unclear.

**Score the summary on:**

1. **Coverage** — every substantive change in the notes (the fix/feature/refactor/
   performance entries with a real library scope) is represented; none silently dropped.
2. **Noise handling** — chore/dependency/test/example/CI entries are not expanded into
   bullets, only collapsed into the trailing count line.
3. **Accuracy** — each bullet correctly states what changed and its effect, consistent
   with the PRs and docs; no invented behaviour.
4. **Breaking changes** — anything that forces action on upgrade (a rename, a removal, or
   a behaviour/output change) is flagged and ends with "(breaking change)"; none omitted
   or softened.
5. **Plain language** — one short sentence per change, no code, no API or identifier
   names, no tooling jargon; readable in a couple of seconds.
6. **Format fallback** — if the rollup did not match the expected release format, the
   summary carries an "unverified" warning; confirm it is present when applicable and
   absent otherwise.

**Recommended method** — derive your own expected change list from the raw notes and the
PRs/docs *independently* of the committed summary, then diff the two. A well-resourced
independent agent (with web and documentation access) is a good reference ceiling; the
goal is that the monitor covers the same substantive changes accurately, not that wording
matches.

**Tool-use spot check** — for a rollup whose PR descriptions are thin, confirm from the
transcript that the model consulted the documentation (doc/web tool calls) rather than
guessing.

## C. Controlled end-to-end test (optional)

To exercise the full pipeline on demand instead of waiting for a real release, publish a
test release in a **sandbox repository you control** and point a temporary config entry at
it:

- Create a few issues with realistic descriptions, then a release whose notes follow the
  same changesets-style format (sections such as Fix / Features, each line referencing an
  issue or PR number, followed by a packages list). Include a chore line to check noise
  handling, and optionally a thin-bodied change to force a documentation lookup.
- Run the workflow and apply sections A and B to the result.
- Remove the test release and temporary config entry afterward.

Never create test releases in the real upstream repositories.

## D. Reporting

State which rollups were checked, the workflow-correctness result per check, and the
quality scores per rollup — with specific examples of any miss (an uncovered change, a
missed breaking-change flag, leaked jargon, a broken file reference). Flag anything that
points to a fix needed in the parser or the summarization prompt.
