#!/usr/bin/env python3
"""Transactional coordinator for deterministic collection publication.

Detection executes against a disposable root.  Canonical state is installed
only after the metadata-only events and receipt validate.  A failed detection
publishes a receipt but deliberately leaves the prior watermark untouched.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import public_ledger


def _json(path):
    return json.loads(path.read_text())


def _canonical_event_equivalent(existing, candidate):
    """An interrupted install may leave the same event with an older stamp."""
    if existing.get("event_id") != candidate.get("event_id"):
        return False
    existing = dict(existing)
    candidate = dict(candidate)
    existing.pop("detected_at_ist", None)
    candidate.pop("detected_at_ist", None)
    return existing == candidate


def _copy_candidate_ledger(candidate, root, created):
    source = candidate / "ledger"
    if not source.exists():
        return
    for path in sorted(source.rglob("*.json")):
        relative = path.relative_to(source)
        parts = relative.parts
        if len(parts) == 2 and parts[0] == "events" and len(parts[1]) == 69 and parts[1].endswith(".json"):
            public_ledger.validate_event(_json(path))
        elif len(parts) == 3 and parts[0] == "collections" and parts[1].count("-") == 2 and parts[2].endswith(".json"):
            public_ledger.validate_receipt(_json(path))
        else:
            raise ValueError(f"candidate ledger path is not allowlisted: {relative}")
        destination = root / "ledger" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and destination.read_bytes() != path.read_bytes():
            if not (parts[0] == "events" and _canonical_event_equivalent(_json(destination), _json(path))):
                raise ValueError(f"canonical ledger collision: {relative}")
        if not destination.exists():
            shutil.copy2(path, destination)
            created.append(destination)


def _install_state(candidate, root):
    state = candidate / "state.json"
    if not state.exists():
        raise ValueError("detector did not produce candidate state")
    # Validate JSON before atomically replacing the canonical watermark.
    _json(state)
    temporary = root / ".state.json.collecting"
    shutil.copy2(state, temporary)
    os.replace(temporary, root / "state.json")


def _install_bundle(candidate, root):
    """Install ledger metadata and watermark as one recoverable logical bundle.

    A normal failure rolls back copied metadata and restores the previous
    watermark.  If a runner is interrupted after an event copy, a later retry
    recognizes the same immutable event identity and completes the bundle.
    """
    before_state = (root / "state.json").read_bytes() if (root / "state.json").exists() else None
    created = []
    try:
        _copy_candidate_ledger(candidate, root, created)
        _install_state(candidate, root)
    except Exception:
        for path in reversed(created):
            path.unlink(missing_ok=True)
        if before_state is None:
            (root / "state.json").unlink(missing_ok=True)
        else:
            temporary = root / ".state.json.rollback"
            temporary.write_bytes(before_state)
            os.replace(temporary, root / "state.json")
        raise


def detect_in_staging(root, candidate, stamp, env=None):
    (candidate / "monitor").mkdir(parents=True)
    shutil.copy2(root / "monitor" / "config.json", candidate / "monitor" / "config.json")
    if (root / "state.json").exists():
        shutil.copy2(root / "state.json", candidate / "state.json")
    child_env = dict(os.environ if env is None else env)
    child_env["MONITOR_ROOT"] = str(candidate)
    child_env["MONITOR_DETECTION_STAMP"] = stamp
    subprocess.run([sys.executable, str(root / "monitor" / "detect.py")], env=child_env, check=True)


def _completed_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_collection(root, stamp, run_id, run_attempt, schedule, completed_at=None,
                   detector=detect_in_staging, trigger="schedule"):
    """Run and publish one collection. Returns ``(receipt, success)``.

    The canonical event/receipt bundle and watermark install together.  Failed
    attempts retain only a failed receipt and never advance the watermark.
    """
    config = _json(root / "monitor" / "config.json")
    try:
        with tempfile.TemporaryDirectory(prefix=".collect-", dir=root) as temp:
            candidate = Path(temp)
            detector(root, candidate, stamp)
            receipt = public_ledger.write_collection(candidate, stamp, config, run_id, run_attempt,
                                                      schedule, "completed", completed_at or _completed_now(), trigger)
            _install_bundle(candidate, root)
            return receipt, True
    except Exception as exc:
        # This path must not inspect candidate raw prose or write candidate state.
        print(f"collection failed safely: {type(exc).__name__}", file=sys.stderr)
        receipt = public_ledger.write_collection(root, stamp, config, run_id, run_attempt,
                                                  schedule, "failed", completed_at or _completed_now(), trigger)
        return receipt, False


def main():
    parser = argparse.ArgumentParser(description="Run the transactional release collector.")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    parser.add_argument("--stamp", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True)
    parser.add_argument("--schedule", required=True)
    parser.add_argument("--completed-at", default="")
    parser.add_argument("--trigger", choices=("schedule", "workflow_dispatch"), default="schedule")
    args = parser.parse_args()
    receipt, success = run_collection(Path(args.root), args.stamp, args.run_id, args.run_attempt,
                                      args.schedule, args.completed_at or None, trigger=args.trigger)
    print(f"collection {receipt['collection_id']} {receipt['outcome']} events={len(receipt['event_ids'])}")
    if not success:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
