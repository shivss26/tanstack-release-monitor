# Testing and rollout gates

## Local deterministic tests

Run before any remote dispatch:

```sh
uv run python -m unittest tests/test_public_ledger.py -v
```

The suite proves: hostile release prose is excluded; URL and schema allowlists
are enforced; a conversion failure does not advance the canonical watermark;
the next run recovers the release once; and the push retry preserves unrelated
upstream commits while refusing concurrent `state.json` or `ledger/` changes.

## Migration-branch Action test

1. Dispatch `workflow_dispatch` from the migration branch using one declared
   slot. It is intentionally a dry run.
2. Confirm successful dry-run logs, then confirm the branch SHA, `state.json`,
   and `ledger/` are unchanged. No staging-generated artifact may be merged.
3. Confirm the last old-workflow `state.json` remains the production starting
   watermark. A release already captured by that workflow must not reappear;
   a new one must appear exactly once after scheduled production collection.
4. Induce a test-only conversion error. Confirm the watermark remains
   unchanged and the following successful scheduled run recovers the same new
   event without a duplicate.

## AgentMail and Work staging gates

Use only the agreed secondary test recipient. Keep the production Work schedule
and production recipient disabled.

1. Create the dedicated monitoring inbox and its Send Allow List. In the Work
   task, verify the configured AgentMail plugin selects that inbox at the start
   of the run and immediately before sending. This approved plugin workflow
   does not use an API key; do not claim a broader connector is credential-
   scoped merely because it selected the right sender.
2. Give Work only GitHub read access. Verify it can read the two ledger
   directories but cannot create a branch, commit, or push.
3. Verify whether the AgentMail send tool supports an idempotency key. If not,
   exercise search-before-send and an ambiguous-send delayed-sent-folder case.
4. Seed multiple collection slots and events. Verify one email includes every
   authoritative receipt and referenced event in the `previous_sha..current_sha`
   range, not only the latest slot.
5. Repeat the same run. The exact sent marker must suppress a duplicate. Then
   add another Action commit and verify the next run processes only the new
   Git range. An ambiguous send must not advance the SHA cursor.
6. Test quiet and catch-up dates. A date whose actual collected receipts all
   contain no events may contain `No changes on this day.`
7. Run shadow collections spanning multiple scheduled slots before merge.

## Production enablement

Proceed only after an independent security/code review, the staging checks,
and a reviewed merge that includes the final old-workflow watermark. Rollback
is to pause Work, disconnect or disable the dedicated AgentMail plugin/inbox,
and disable the new Actions schedule; do not delete ledger or sent-mail
evidence.
