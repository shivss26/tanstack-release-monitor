# Public ledger schema

The public ledger is metadata-only JSON produced by GitHub Actions. Schema
version `1` has two record types. Release bodies, pull-request text, model
output, provider metadata, mailbox data, and credentials are forbidden.

## Release event

Path: `ledger/events/<sha256(event_id)>.json`

Required fields:

| Field | Shape |
|---|---|
| `schema_version` | Integer `1`. |
| `event_id` | Stable identity: `<kind>:<source-label>:<release-id[,release-id...]>`. |
| `kind` | `release`, `release-batch`, or `retraction`. |
| `source` | Object containing allowlisted `label` and `repository`. |
| `detected_at_ist` | ISO-8601 timestamp with the IST offset. |
| `releases` | Non-empty array of release metadata objects. |

Optional fields:

| Field | Shape |
|---|---|
| `filtered_packages` | Sorted non-React package tags filtered out of an accepted package batch. |
| `retraction` | Boolean `true` for a retraction event. |

Each release metadata object contains a positive GitHub release `id`, safe
`tag`, UTC `published_at` timestamp (which may be absent for a retraction), and
the canonical `https://github.com/TanStack/.../releases/tag/...` URL.

## Collection receipt

Path:
`ledger/collections/<YYYY-MM-DD>/<HHMM>--<run-id>-<attempt>.json`

Every receipt contains exactly these fields:

| Field | Shape |
|---|---|
| `schema_version` | Integer `1`. |
| `collection_id` | `github-actions:<run-id>:<attempt>`. |
| `collection_date_ist` | IST calendar date. |
| `scheduled_slot_ist` | One of `00:00`, `04:00`, `08:00`, `12:00`, `16:00`, `20:00`. |
| `outcome` | `completed` or `failed`. |
| `completed_at_utc` | UTC completion timestamp. |
| `trigger` | `schedule` or `workflow_dispatch`. |
| `authoritative` | `true` only for scheduled runs. |
| `event_ids` | Sorted unique event identities; empty on failure. |

Manual dry-run receipts are non-authoritative and are not committed by the
workflow. Failed scheduled receipts are committed without advancing the
detector watermark.

## Delivery read contract

ChatGPT Work captures a fixed `current_sha`, finds `previous_sha` in the final
line of the newest successful AgentMail report, and reads the receipts plus
referenced events introduced by `previous_sha..current_sha`. It ignores
non-authoritative receipts and does not read `obsolete/` or raw release prose.
The sent email ends with `<!-- tsrm-main-sha:CURRENT_SHA -->`; that sent copy is
the only delivery cursor.
