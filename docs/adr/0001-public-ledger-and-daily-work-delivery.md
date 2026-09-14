# ADR 0001: Public ledger and daily Work delivery

Status: accepted

## Decision

GitHub Actions remains the deterministic collector because it can run the
proven Python helpers and durably commit their state. It writes a compact,
sanitized public ledger rather than raw API payloads or delivery data. A ChatGPT
Work task at 22:00 IST is the separate judgement and AgentMail delivery layer,
so it can aggregate all undelivered events without API-model credits or an
always-on personal device.

GitHub Actions commits scheduled collection receipts, events, and `state.json`
directly to `main`. ChatGPT Work is read-only and processes the Git range after
the SHA marker in the previous successful AgentMail report through a captured
current `main` SHA. Delayed Work execution therefore enlarges one range; it
does not require a second delivery ledger or one branch per collection.

## Consequences

- The active repository schema is documented in `docs/ledger-schema.md`.
- Runtime scripts use only the Python standard library and run as `python3`.
- Old raw, enrichment, transcript, digest, and send artifacts are non-canonical
  and temporarily retained only under `obsolete/` for rollout reference.
- Delivery advances only when the AgentMail sent copy contains the final SHA
  marker. A failed or ambiguous delivery leaves the cursor unchanged.
