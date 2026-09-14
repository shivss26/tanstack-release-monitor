#!/usr/bin/env python3
"""Strict, metadata-only public ledger helpers.

GitHub release bodies are untrusted third-party text. They are deliberately
excluded from the public ledger and from the mail-capable Work task.
"""
import argparse
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

IST = timezone(timedelta(hours=5, minutes=30))
SCHEMA_VERSION = 1
STAMP_RE = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})-(?P<hour>[0-2]\d)(?P<minute>[0-5]\d)$")
TAG_RE = re.compile(r"^[A-Za-z0-9@._/-]{1,180}$")
PACKAGE_RE = re.compile(r"^@tanstack/[a-z0-9-]{1,100}@\d+\.\d+\.\d+$")
ISO_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
SLOTS = {
    "30 18 * * *": "00:00", "30 22 * * *": "04:00", "30 2 * * *": "08:00",
    "30 6 * * *": "12:00", "30 10 * * *": "16:00", "30 14 * * *": "20:00",
}


def _json(path):
    return json.loads(path.read_text())


def _source_map(config):
    result = {}
    for source in config["sources"]:
        label = source["label"]
        if not re.fullmatch(r"[a-z0-9-]{1,80}", label):
            raise ValueError(f"unsafe source label: {label!r}")
        result[label] = {"label": label, "repository": f"{source['owner']}/{source['repo']}"}
    return result


def _stamp_to_ist(stamp):
    if not STAMP_RE.fullmatch(stamp):
        raise ValueError(f"invalid detector stamp: {stamp!r}")
    return datetime.strptime(stamp, "%Y-%m-%d-%H%M").replace(tzinfo=IST)


def _release(release, source, *, allow_missing_published_at=False):
    if not isinstance(release, dict):
        raise ValueError("release must be an object")
    release_id = release.get("id")
    tag = release.get("tag_name") or release.get("tag")
    published_at = release.get("published_at")
    url = release.get("html_url")
    if not isinstance(release_id, int) or release_id <= 0:
        raise ValueError("release id must be a positive integer")
    if not isinstance(tag, str) or not TAG_RE.fullmatch(tag):
        raise ValueError("release tag is malformed")
    if published_at is None and allow_missing_published_at:
        pass
    elif not isinstance(published_at, str) or not ISO_UTC_RE.fullmatch(published_at):
        raise ValueError("release publication timestamp is malformed")
    parsed = urlparse(url) if isinstance(url, str) else None
    expected_path = f"/{source['repository']}/releases/tag/{tag}"
    if not parsed or parsed.scheme != "https" or parsed.netloc != "github.com" or parsed.query or parsed.fragment or unquote(parsed.path) != expected_path:
        raise ValueError("release URL is not the canonical repository release URL")
    return {"id": release_id, "tag": tag, "published_at": published_at, "url": url}


def _event_id(kind, label, release_ids):
    return f"{kind}:{label}:{','.join(str(value) for value in release_ids)}"


def _event_file(event_id):
    return hashlib.sha256(event_id.encode("utf-8")).hexdigest() + ".json"


def _event(kind, source, stamp, releases, filtered_packages=(), retraction=False):
    release_ids = [item["id"] for item in releases]
    if not release_ids or len(set(release_ids)) != len(release_ids):
        raise ValueError("event must contain unique release ids")
    event_id = _event_id(kind, source["label"], release_ids)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "event_id": event_id,
        "kind": kind,
        "source": source,
        "detected_at_ist": _stamp_to_ist(stamp).isoformat(),
        "releases": releases,
    }
    if filtered_packages:
        if not all(isinstance(item, str) and PACKAGE_RE.fullmatch(item) for item in filtered_packages):
            raise ValueError("filtered package name is malformed")
        payload["filtered_packages"] = sorted(filtered_packages)
    if retraction:
        payload["retraction"] = True
    validate_event(payload)
    return event_id, payload


def _raw_payload(path, source, stamp):
    raw = _json(path)
    if not isinstance(raw, dict):
        raise ValueError(f"raw record is not an object: {path}")
    if raw.get("style") == "package-batch":
        releases = [_release(value, source) for value in raw.get("releases", [])]
        return _event("release-batch", source, stamp, releases, raw.get("excluded_tags", []))
    release = raw.get("release") if raw.get("style") == "single-package" else raw
    return _event("release", source, stamp, [_release(release, source)])


def _yank_payload(path, source, stamp):
    fields = {}
    for line in path.read_text().splitlines():
        if line.startswith("- release id: "):
            fields["id"] = line.removeprefix("- release id: ")
        elif line.startswith("- tag: "):
            fields["tag"] = line.removeprefix("- tag: ")
        elif line.startswith("- originally published: "):
            fields["published_at"] = line.removeprefix("- originally published: ")
    try:
        release_id = int(fields["id"])
    except (KeyError, ValueError) as exc:
        raise ValueError(f"invalid yank record: {path}") from exc
    tag = fields.get("tag")
    release = {"id": release_id, "tag": tag, "published_at": fields.get("published_at"),
               "html_url": f"https://github.com/{source['repository']}/releases/tag/{tag}"}
    return _event("retraction", source, stamp,
                  [_release(release, source, allow_missing_published_at=True)], retraction=True)


def discover_events(root, stamp, config):
    sources = _source_map(config)
    events = []
    for label, source in sources.items():
        raw_dir = root / "raw" / label
        for path in sorted(raw_dir.glob(f"{stamp}__*.json")) if raw_dir.exists() else []:
            event_id, payload = _raw_payload(path, source, stamp)
            events.append({"event_id": event_id, "payload": payload})
        yank_dir = root / "yanks" / label
        for path in sorted(yank_dir.glob(f"{stamp}__*.md")) if yank_dir.exists() else []:
            event_id, payload = _yank_payload(path, source, stamp)
            events.append({"event_id": event_id, "payload": payload})
    ids = [item["event_id"] for item in events]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate event identity in one collection")
    return sorted(events, key=lambda item: item["event_id"])


def slot_from_schedule(schedule):
    if schedule not in SLOTS:
        raise ValueError(f"unknown scheduled slot: {schedule!r}")
    return SLOTS[schedule]


def _receipt(stamp, run_id, run_attempt, schedule, outcome, completed_at, event_ids):
    if outcome not in {"completed", "failed"}:
        raise ValueError(f"unsupported collection outcome: {outcome!r}")
    if not isinstance(run_id, str) or not run_id.isdecimal() or not str(run_attempt).isdecimal():
        raise ValueError("invalid GitHub run identity")
    if not isinstance(completed_at, str) or not ISO_UTC_RE.fullmatch(completed_at):
        raise ValueError("invalid completion timestamp")
    stamp_ist = _stamp_to_ist(stamp)
    receipt = {"schema_version": SCHEMA_VERSION,
               "collection_id": f"github-actions:{run_id}:{run_attempt}",
               "collection_date_ist": stamp_ist.date().isoformat(),
               "scheduled_slot_ist": slot_from_schedule(schedule),
               "outcome": outcome, "completed_at_utc": completed_at,
               "event_ids": sorted(event_ids) if outcome == "completed" else []}
    validate_receipt(receipt)
    return receipt


def write_collection(root, stamp, config, run_id, run_attempt, schedule, outcome, completed_at):
    events = discover_events(root, stamp, config) if outcome == "completed" else []
    receipt = _receipt(stamp, str(run_id), str(run_attempt), schedule, outcome, completed_at,
                       [event["event_id"] for event in events])
    events_dir = root / "ledger" / "events"
    for event in events:
        events_dir.mkdir(parents=True, exist_ok=True)
        path = events_dir / _event_file(event["event_id"])
        if path.exists() and _json(path) != event["payload"]:
            raise ValueError(f"event collision: {event['event_id']}")
        path.write_text(json.dumps(event["payload"], indent=2, sort_keys=True) + "\n")
    slot = receipt["scheduled_slot_ist"].replace(":", "")
    receipt_dir = root / "ledger" / "collections" / receipt["collection_date_ist"]
    receipt_dir.mkdir(parents=True, exist_ok=True)
    (receipt_dir / f"{slot}--{run_id}-{run_attempt}.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    return receipt


def validate_event(payload):
    required = {"schema_version", "event_id", "kind", "source", "detected_at_ist", "releases"}
    allowed = required | {"filtered_packages", "retraction"}
    if set(payload) - allowed or not required <= set(payload):
        raise ValueError("event schema keys are invalid")
    if payload["schema_version"] != SCHEMA_VERSION or payload["kind"] not in {"release", "release-batch", "retraction"}:
        raise ValueError("event schema values are invalid")
    source = payload["source"]
    if set(source) != {"label", "repository"} or not re.fullmatch(r"[a-z0-9-]{1,80}", source["label"]):
        raise ValueError("event source is invalid")
    if not re.fullmatch(r"TanStack/[a-z0-9-]{1,100}", source["repository"]):
        raise ValueError("event repository is invalid")
    if not isinstance(payload["releases"], list) or not payload["releases"]:
        raise ValueError("event releases are invalid")
    for release in payload["releases"]:
        if set(release) != {"id", "tag", "published_at", "url"}:
            raise ValueError("release schema keys are invalid")
        _release({"id": release["id"], "tag": release["tag"], "published_at": release["published_at"],
                  "html_url": release["url"]}, source,
                 allow_missing_published_at=payload["kind"] == "retraction")


def validate_receipt(receipt):
    required = {"schema_version", "collection_id", "collection_date_ist", "scheduled_slot_ist", "outcome", "completed_at_utc", "event_ids"}
    if set(receipt) != required or receipt["schema_version"] != SCHEMA_VERSION:
        raise ValueError("receipt schema keys are invalid")
    if receipt["scheduled_slot_ist"] not in set(SLOTS.values()) or receipt["outcome"] not in {"completed", "failed"}:
        raise ValueError("receipt schema values are invalid")
    if not ISO_UTC_RE.fullmatch(receipt["completed_at_utc"]):
        raise ValueError("receipt timestamp is invalid")
    if not isinstance(receipt["event_ids"], list) or receipt["event_ids"] != sorted(set(receipt["event_ids"])):
        raise ValueError("receipt event ids are invalid")


def main():
    parser = argparse.ArgumentParser(description="Write a metadata-only public collection ledger.")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    parser.add_argument("--config", default="")
    parser.add_argument("--stamp", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True)
    parser.add_argument("--schedule", required=True)
    parser.add_argument("--outcome", required=True, choices=("completed", "failed"))
    parser.add_argument("--completed-at", required=True)
    args = parser.parse_args()
    root = Path(args.root)
    config_path = Path(args.config) if args.config else root / "monitor" / "config.json"
    receipt = write_collection(root, args.stamp, _json(config_path), args.run_id, args.run_attempt,
                               args.schedule, args.outcome, args.completed_at)
    print(f"collection {receipt['collection_id']} {receipt['outcome']} events={len(receipt['event_ids'])}")


if __name__ == "__main__":
    main()
