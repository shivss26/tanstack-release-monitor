# TanStack Release Monitoring

This context records the durable facts used to monitor selected public TanStack
release streams and provide one daily human-facing update.

## Language

**Collection**:
One expected deterministic scan of every configured source at a scheduled IST
slot. A collection has a result even when it discovers no release.
_Avoid_: Check, session, task

**Release Event**:
A stable record that a configured source published a reportable release batch or
retracted a previously seen release.
_Avoid_: Raw payload, update file

**Collection Receipt**:
The durable record of a Collection's observed outcome and its Release Events.
_Avoid_: Log, marker

**Delivery**:
One daily or catch-up email that accounts for a fixed set of Release Events.
_Avoid_: Send, notification run

**Coverage**:
The set of expected Collections whose outcomes are known by an email cutoff.
_Avoid_: Success rate, uptime

## Canonical records

Only `state.json` and the versioned records under `ledger/` are canonical
repository state. AgentMail sent mail is the Delivery record and carries the
last successfully delivered `main` SHA. Files under `obsolete/` are temporary
legacy reference material; they are never read by the current implementation.
