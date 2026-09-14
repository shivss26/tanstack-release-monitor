# TanStack Release Monitor

This public project watches selected TanStack repositories for stable releases
relevant to a React stack. It runs without the owner’s computer being online.
The runtime is standard-library Python and requires no package installation.

## Architecture

```text
GitHub Actions at six IST slots
  -> deterministic detector in an isolated staging root
  -> validated, metadata-only public ledger and canonical watermark
  -> ChatGPT Work at 22:00 IST (GitHub read-only)
  -> one short factual daily email through a dedicated AgentMail inbox
  -> AgentMail sent folder is the delivery record
```

GitHub Actions runs at 00:00, 04:00, 08:00, 12:00, 16:00, and 20:00 IST. Each
scheduled run commits its receipt and sanitized events directly to `main`. At
22:00 IST, ChatGPT Work reports every collection actually committed between its
previous sent SHA marker and a captured current `main` SHA. If Work is delayed,
the next email simply covers the larger Git range; it does not invent absent
Action runs.

## Safety and consistency properties

- The collector performs detection in a disposable root. It installs the
  validated public ledger bundle and real `state.json` recoverably; a failed
  install restores the prior watermark, and a retry recognizes the same event
  identity instead of skipping it.
- GitHub Actions is the only writer to GitHub. ChatGPT Work has read-only
  GitHub access and never creates delivery commits or changes detector state.
- Ledger events contain only validated source identity, release ID/tag/time,
  canonical GitHub release URL, package names, and retraction status. Release
  bodies, PR text, model output, provider metadata, and credentials are absent.
- Delivery uses the sent AgentMail report as a single Git commit cursor. Work
  reads receipts/events introduced after the previous sent report's `main` SHA
  through a captured current `main` SHA. A failed or absent Work run therefore
  catches up naturally without a second delivery ledger.

## Public-repository boundary

Action commits stage only `state.json` and `ledger/`. The previous
Claude-and-Resend pipeline and its historical outputs are quarantined under
`obsolete/` as a temporary rollback reference while the replacement operates
in production. Nothing under `obsolete/` is runtime input or canonical state.
Never place mailbox addresses, API keys, delivery IDs, or private data in this
repository.

## Layout

| Path | Purpose |
|---|---|
| `monitor/detect.py` | Existing deterministic detector and React filtering. |
| `monitor/collect.py` | Transaction boundary between candidate detection and canonical state/ledger. |
| `monitor/public_ledger.py` | Metadata-only event and receipt schema and validation. |
| `monitor/publish.py` | Bounded push retry that fails closed on state/ledger overlap. |
| `ledger/events/` | Validated release and retraction events. |
| `ledger/collections/` | One collection receipt per Action attempt. |
| `docs/ledger-schema.md` | Versioned public event and collection receipt schema. |
| `docs/chatgpt-work-task-template.md` | Read-only Work delivery instructions. |
| `PERFORMANCE-REVIEW-HANDOFF.md` | Read-only checklist for reviewing the first production runs. |
| `obsolete/` | Temporary, non-canonical backup of the retired pipeline. |

## Verification

```sh
python3 -m unittest tests/test_public_ledger.py -v
```

See [`TESTING.md`](./TESTING.md) for the reusable rollout gates and the
production verification recorded when this architecture went live.
