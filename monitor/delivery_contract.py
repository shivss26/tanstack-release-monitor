"""Pure, versioned delivery identity contract for the Work task and tests."""
import base64
import hashlib
import json
import re

SCHEMA_VERSION = 2
FOOTER_PREFIX = "<!-- tsrm-manifest-v2:"
FOOTER_SUFFIX = " -->"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SLOTS = ("00:00", "04:00", "08:00", "12:00", "16:00", "20:00")
OUTCOMES = frozenset({"completed", "failed", "missing"})


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _validate_slot_outcomes(covered_dates, slot_outcomes):
    if not isinstance(slot_outcomes, list) or len(slot_outcomes) != len(covered_dates) * len(SLOTS):
        raise ValueError("manifest requires exactly six slot outcomes per covered date")
    expected = [(date, slot) for date in covered_dates for slot in SLOTS]
    actual = []
    receipt_ids = []
    for item in slot_outcomes:
        if not isinstance(item, dict) or set(item) != {"date", "slot", "outcome", "receipt_id"}:
            raise ValueError("slot outcome schema is invalid")
        date, slot, outcome, receipt_id = item["date"], item["slot"], item["outcome"], item["receipt_id"]
        if not isinstance(date, str) or not DATE_RE.fullmatch(date) or slot not in SLOTS or outcome not in OUTCOMES:
            raise ValueError("slot outcome values are invalid")
        if outcome == "missing":
            if receipt_id is not None:
                raise ValueError("missing slot cannot have a receipt identity")
        elif not isinstance(receipt_id, str) or not receipt_id:
            raise ValueError("received slot requires a receipt identity")
        else:
            receipt_ids.append(receipt_id)
        actual.append((date, slot))
    if actual != expected:
        raise ValueError("slot outcomes must be ordered by date then canonical slot")
    if len(receipt_ids) != len(set(receipt_ids)):
        raise ValueError("receipt identities must be unique")
    return sorted(receipt_ids)


def manifest(covered_dates, slot_outcomes, event_ids, recipient_alias):
    if not isinstance(recipient_alias, str) or not recipient_alias:
        raise ValueError("recipient alias is required")
    if (not isinstance(covered_dates, list) or not covered_dates
            or covered_dates != sorted(set(covered_dates))
            or not all(isinstance(date, str) and DATE_RE.fullmatch(date) for date in covered_dates)):
        raise ValueError("covered dates must be sorted, unique ISO dates")
    if (not isinstance(event_ids, list) or event_ids != sorted(set(event_ids))
            or not all(isinstance(event_id, str) and event_id for event_id in event_ids)):
        raise ValueError("event identities must be sorted, unique strings")
    receipt_ids = _validate_slot_outcomes(covered_dates, slot_outcomes)
    return {
        "schema_version": SCHEMA_VERSION,
        "covered_dates": covered_dates,
        "slot_outcomes": slot_outcomes,
        "event_ids": event_ids,
        "receipt_ids": receipt_ids,
        "recipient_alias": recipient_alias,
    }


def validate_manifest(value):
    if not isinstance(value, dict) or set(value) != {
        "schema_version", "covered_dates", "slot_outcomes", "event_ids", "receipt_ids", "recipient_alias"
    } or value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("manifest schema is invalid")
    rebuilt = manifest(value["covered_dates"], value["slot_outcomes"], value["event_ids"], value["recipient_alias"])
    if rebuilt != value:
        raise ValueError("manifest is not canonical")
    return value


def delivery_key(value):
    validate_manifest(value)
    return "tsrm-v2-" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def footer(value):
    validate_manifest(value)
    encoded = base64.urlsafe_b64encode(canonical_json(value).encode("ascii")).decode("ascii")
    return FOOTER_PREFIX + encoded + FOOTER_SUFFIX


def parse_footer(body):
    lines = body.splitlines()
    matches = [line for line in lines if line.startswith(FOOTER_PREFIX) and line.endswith(FOOTER_SUFFIX)]
    if len(matches) != 1:
        return None
    encoded = matches[0][len(FOOTER_PREFIX):-len(FOOTER_SUFFIX)]
    try:
        decoded = base64.urlsafe_b64decode(encoded.encode("ascii"))
        value = json.loads(decoded)
        validate_manifest(value)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if canonical_json(value).encode("ascii") == decoded else None


def exact_sent_match(message, expected_sender, expected_recipient, expected_subject, expected_manifest):
    """Reject inbox mail, spoofed subject lines, and non-canonical footers."""
    return (message.get("location") == "sent"
            and message.get("from") == expected_sender
            and message.get("to") == [expected_recipient]
            and message.get("subject") == expected_subject
            and parse_footer(message.get("body", "")) == expected_manifest)
