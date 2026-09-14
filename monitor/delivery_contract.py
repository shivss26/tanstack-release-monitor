"""Pure delivery identity contract shared by tests and the Work task specification."""
import base64
import hashlib
import json

SCHEMA_VERSION = 1
FOOTER_PREFIX = "<!-- tsrm-manifest-v1:"
FOOTER_SUFFIX = " -->"


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def manifest(covered_dates, slot_outcomes, event_ids, recipient_alias):
    if not isinstance(recipient_alias, str) or not recipient_alias:
        raise ValueError("recipient alias is required")
    value = {"schema_version": SCHEMA_VERSION, "covered_dates": sorted(set(covered_dates)),
             "slot_outcomes": slot_outcomes, "event_ids": sorted(set(event_ids)),
             "recipient_alias": recipient_alias}
    if value["covered_dates"] != list(covered_dates) or value["event_ids"] != list(event_ids):
        raise ValueError("manifest inputs must already be sorted and unique")
    return value


def delivery_key(value):
    return "tsrm-v1-" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def footer(value):
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
