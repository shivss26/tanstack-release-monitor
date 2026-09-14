# Production performance review handoff

Use this handoff in a fresh session after several days of production operation.
Begin read-only: do not dispatch workflows, edit the repository, alter the Work
task, or send email while collecting evidence.

## Baseline

- Production cutover: 14 September 2026.
- First scheduled collector: GitHub Actions run `34868964023`, ledger commit
  `9a8b693`.
- First production-shaped delivery: `a1e998e..1633bc9`, covering one
  authoritative collection, two event records, and three releases.
- The sent copy and receiving inbox both contained the standalone `1633bc9`
  cursor marker followed only by the AgentMail-managed footer.
- The standalone ChatGPT Work task was left active daily at 22:00
  `Asia/Kolkata`, with read-only GitHub and the dedicated AgentMail connection.
- Later documentation commit `7dba4f6` contains no ledger event.

Provider account identifiers, mailbox addresses, message IDs, and the Work task
ID are intentionally absent from this public repository. Read them from the
configured services only if they are necessary for verification.

## Review procedure

1. Record the review window and capture current `origin/main` before inspecting
   results.
2. List every scheduled collector run in the window. Match each completed run
   to its committed collection receipt and confirm the receipt's outcome,
   authority flag, event references, and GitHub run identity.
3. Validate that every referenced event exists, is metadata-only, and appears
   exactly once. Reconcile release tags, timestamps, canonical URLs, and
   retraction flags against the receipts without reading release prose.
4. List the Work task's scheduled occurrences and the corresponding AgentMail
   sent records and received messages. Do not send a test message.
5. Check cursor continuity: each successful report's `previous_sha` must equal
   the prior valid standalone sent marker, and its `current_sha` must bound all
   authoritative receipts it reports. A delayed or skipped occurrence is valid
   only when the next successful report covers the entire larger range.
6. Confirm one health line per authoritative collection, complete event
   coverage, no duplicated release, no duplicate email for the same current
   SHA, and correct quiet-day wording.
7. Report exact counts for Action runs, authoritative receipts, event records,
   releases, scheduled Work occurrences, sent emails, received emails,
   duplicates, gaps, and failures. Separate observed facts from inference.

Conclude with `pass`, `pass with observations`, or `fail`, list every mismatch,
and recommend changes only after identifying a concrete failure. Do not modify
the deterministic detector merely because it has remained unchanged.
