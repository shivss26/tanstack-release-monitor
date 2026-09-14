# Obsolete pipeline reference

This directory is a temporary rollback and investigation reference for the
retired Claude-and-Resend pipeline. It is not part of the current runtime and
nothing here is canonical state.

Contents:

- `raw/` and `yanks/`: historical detector source evidence.
- `prefetch/`, `summaries/`, and `transcripts/`: retired enrichment inputs and
  outputs.
- `digest/` and `sent/`: retired email artifacts and delivery markers.
- `TESTBENCH-PLAN.md`: historical validation evidence for that pipeline.

The current implementation must read only `state.json`, `ledger/`, and its
static configuration. After the new Actions → ledger → Work → AgentMail flow
has operated successfully for the agreed observation period, this directory
can be deleted in a separate cleanup commit; Git history will still preserve
it.
