# TanStack Release Monitor

A scheduled service that watches selected TanStack repositories for new stable
releases relevant to a **React** stack and publishes a concise, plain-language
summary of what changed in each. It runs entirely on GitHub Actions — no
always-on server or local machine required.

## How it works

Each run has four stages:

1. **Detect** — polls each configured repository's GitHub Releases, deduplicating
   against a small state file so nothing is processed twice. Each source declares
   one of three deterministic styles:
   - **rollup** — repos that publish one aggregate release per publish
     (`release-YYYY-MM-DD-HHMM`, or plain `vX.Y.Z` for table). Removed (yanked)
     releases are also tracked.
   - **single-package** — one-package repos with plain `vX.Y.Z` stable tags (intent).
   - **package-batch** — changesets repos that publish one GitHub release per
     package (form, db, ai, virtual, store, pacer). New stable releases are grouped
     into one batch per publish (a settle delay keeps a publish from splitting
     across runs) and filtered to React-relevant packages — unprefixed core
     packages and `react-*`; vue/solid/svelte/preact/lit/angular/marko/qwik
     adapters are dropped.
   Prereleases and drafts are never picked up, and changes scoped to another
   framework are treated as noise everywhere.
2. **Gate** — counts how many recorded items still need a summary. The heavier
   summarization stage runs only when there is work to do, keeping routine runs cheap.
3. **Summarize** — for each pending item, gathers the release notes and the relevant
   pull-request descriptions (deduplicating a change that appears in several
   packages' notes), then uses a language model (with web search and page fetch)
   to write one short bullet per meaningful change, flagging anything that breaks
   existing usage. The result is committed as a Markdown summary plus ONE combined
   digest per run. If a release does not match the expected format, the summary
   falls back to the raw notes and is clearly marked as unverified.
4. **Deliver** — emails the digest (all of a run's summaries in one email; a quiet
   run sends nothing). Delivery is recorded once it succeeds, so a failed send is
   retried on the next run rather than lost or duplicated.

The pipeline is decoupled and self-healing: an interrupted run leaves its work marked
as pending, and the next run retries it. A run that finds nothing new produces no output.

## Repository layout

| Path | Purpose |
|---|---|
| `monitor/detect.py` | Release and removal detection, per-source styles, React filter (standard library only). |
| `monitor/pending.py` | Counts outstanding work; gates the summarization stage. |
| `monitor/prefetch.py` | Assembles release notes and pull-request context per style. |
| `monitor/enrich.py` | Language-model summarization (web search/fetch tools) and output assembly. |
| `monitor/enrich_run.py` | Orchestrates detection output into summaries and a digest. |
| `monitor/send.py` | Emails unsent digests (standard library only). |
| `monitor/system-prompt.md` | Summarization instructions. |
| `monitor/config.json` | Repositories to watch, with per-source style options. |
| `.github/workflows/monitor.yml` | The scheduled workflow. |
| `raw/`, `summaries/`, `digest/`, `sent/`, … | Generated records, output, and delivery markers. |

## Configuration

- **Watched repositories** live in `monitor/config.json` — each entry names an owner,
  a repository, and the libraries it covers.
- **Schedule** is the cron in `.github/workflows/monitor.yml`; remove it to run on demand only.
- Summarization requires an Anthropic API key; email delivery requires an email-provider
  key and a recipient address. All are supplied to the workflow as encrypted secrets and
  never live in the repository.

## Maintenance

Dependencies are pinned and do not update on their own.

- **Monthly:** review the pinned Anthropic SDK version (in `pyproject.toml` and
  `uv.lock`). Python dependencies enforce a minimum 7-day release age
  automatically via the lockfile.
- **Occasionally (~6 months):** confirm the configured model identifier and the web-tool
  versions in `monitor/enrich.py` are still current, and update any that have been retired.
- **Failure handling:** GitHub notifies the repository owner when a scheduled run fails.
  A one-off failure self-heals on the next run; repeated failures point to a pin or
  credential that needs attention.
- **Format changes:** if an upstream project changes its release format, affected
  summaries are marked unverified rather than failing silently — a cue to update the parser.

## Local development

The scripts target Python 3.13 with `uv`. Detection uses only the standard library;
summarization installs its dependencies from the committed lockfile. Toolchain versions
are pinned in `mise.toml`.

## Status

Each run commits the summaries and a combined digest, and emails the digest. A digest is
recorded as delivered once sent, so a delivery failure is retried on the next run rather
than lost or duplicated.
