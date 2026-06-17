# TanStack Release Monitor

A scheduled service that watches selected TanStack repositories for new release
rollups and publishes a concise, plain-language summary of what changed in each.
It runs entirely on GitHub Actions — no always-on server or local machine required.

## How it works

Each run has three stages:

1. **Detect** — polls each configured repository's GitHub Releases for new release
   rollups and for releases that have been removed, deduplicating against a small
   state file so nothing is processed twice. New items are recorded verbatim.
2. **Gate** — counts how many recorded items still need a summary. The heavier
   summarization stage runs only when there is work to do, keeping routine runs cheap.
3. **Summarize** — for each new rollup, gathers the release notes and the relevant
   pull-request descriptions, then uses a language model (with access to the official
   TanStack documentation and web search) to write one short bullet per meaningful
   change, flagging anything that breaks existing usage. The result is committed as a
   Markdown summary plus a combined digest. If a rollup does not match the expected
   release format, the summary falls back to the raw notes and is clearly marked as
   unverified.

The pipeline is decoupled and self-healing: an interrupted run leaves its work marked
as pending, and the next run retries it. A run that finds nothing new produces no output.

## Repository layout

| Path | Purpose |
|---|---|
| `monitor/detect.py` | Release and removal detection (standard library only). |
| `monitor/pending.py` | Counts outstanding work; gates the summarization stage. |
| `monitor/prefetch.py` | Assembles release notes and pull-request context. |
| `monitor/enrich.py` | Language-model summarization and output assembly. |
| `monitor/enrich_run.py` | Orchestrates detection output into summaries and a digest. |
| `monitor/tanstack_tool.py` | Documentation lookup backed by the pinned TanStack CLI. |
| `monitor/system-prompt.md` | Summarization instructions. |
| `monitor/config.json` | Repositories to watch. |
| `.github/workflows/monitor.yml` | The scheduled workflow. |
| `raw/`, `summaries/`, `digest/`, … | Generated records and output. |

## Configuration

- **Watched repositories** live in `monitor/config.json` — each entry names an owner,
  a repository, and the libraries it covers.
- **Schedule** is the cron in `.github/workflows/monitor.yml`; remove it to run on demand only.
- Summarization requires an Anthropic API key, supplied to the workflow as an encrypted
  secret.

## Maintenance

Dependencies are pinned and do not update on their own.

- **Monthly:** review the two pinned versions — the Anthropic SDK (in `pyproject.toml`
  and `uv.lock`) and the TanStack CLI (in the workflow and `monitor/tanstack_tool.py`,
  kept in sync).
  - Python dependencies enforce a minimum 7-day release age automatically via the lockfile.
  - The TanStack CLI has no automatic age gate; when bumping it, manually pick a version
    at least 7 days old.
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

Summaries are committed to the repository. Email delivery is planned.
