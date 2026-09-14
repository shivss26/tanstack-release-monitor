# TanStack Release Monitor

This public project watches selected TanStack repositories for stable releases
relevant to a React stack. It runs without the owner’s computer being online.

## Architecture

```text
GitHub Actions at six IST slots
  -> deterministic detector in an isolated staging root
  -> validated, metadata-only public ledger and canonical watermark
  -> ChatGPT Work at 22:00 IST (GitHub read-only)
  -> one short factual daily email through a dedicated AgentMail inbox
  -> AgentMail sent folder is the delivery record
```

GitHub Actions runs at 00:00, 04:00, 08:00, 12:00, 16:00, and 20:00 IST. It
records a receipt for every slot. A failed or absent slot is never described as
quiet. At 22:00 IST, ChatGPT Work sends one daily email with six health lines;
an entirely complete quiet day ends with `No changes on this day.` A later Work
run sends a single catch-up email covering every undelivered IST date.

## Safety and consistency properties

- The collector performs detection in a disposable root. It writes the real
  `state.json` only after all public events and a collection receipt validate.
  A conversion failure produces only a failed receipt, so a later run can
  recover the release instead of skipping it.
- GitHub Actions is the only writer to GitHub. ChatGPT Work has read-only
  GitHub access and never creates delivery commits or changes detector state.
- Ledger events contain only validated source identity, release ID/tag/time,
  canonical GitHub release URL, package names, and retraction status. Release
  bodies, PR text, model output, provider metadata, and credentials are absent.
- Delivery uses a canonical manifest and stable key. Work accepts prior delivery
  only after an exact sent-folder match (sender, recipient, subject/key, and
  manifest footer). This is at-least-once delivery with duplicate suppression,
  not an unsupported exactly-once claim.

## Public-repository boundary

Future Action commits stage only `state.json` and `ledger/`. The old pipeline’s
`raw/`, `yanks/`, `prefetch/`, `summaries/`, `transcripts/`, `digest/`, and
`sent/` paths are ignored for future output. Never place mailbox addresses,
API keys, delivery IDs, or private data in this repository.

## Layout

| Path | Purpose |
|---|---|
| `monitor/detect.py` | Existing deterministic detector and React filtering. |
| `monitor/collect.py` | Transaction boundary between candidate detection and canonical state/ledger. |
| `monitor/public_ledger.py` | Strict metadata-only event and receipt schema. |
| `monitor/publish.py` | Bounded push retry that fails closed on state/ledger overlap. |
| `monitor/delivery_contract.py` | Canonical manifest, key, footer, and sent-record matching contract. |
| `ledger/events/` | Validated release and retraction events. |
| `ledger/collections/` | One collection receipt per Action attempt. |
| `docs/chatgpt-work-task-template.md` | Read-only Work delivery instructions. |

## Verification

```sh
uv run python -m unittest tests/test_public_ledger.py -v
```

Follow [`TESTING.md`](./TESTING.md) before enabling the production Work
schedule or recipient.
