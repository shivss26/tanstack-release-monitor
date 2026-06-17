#!/usr/bin/env python3
"""Email delivery — decoupled and self-healing.

Each digest written by the enrich stage (`digest/<run-stamp>.md`) is emailed once.
A successful send is recorded as `sent/<run-stamp>.md`, so a digest with no matching
marker is "unsent" and is retried on the next run — a provider outage never drops a
digest, and a digest is never emailed twice.

Stdlib only (urllib). Delivery uses the Resend API, configured entirely via env:
  RESEND_API_KEY     API key. If unset, sending is skipped cleanly and the rest of the
                     pipeline is unaffected (digests remain unsent for a later run).
  DIGEST_EMAIL_TO    Recipient address. Also required to send.
  DIGEST_EMAIL_FROM  Sender (optional). Defaults to a resend.dev test sender, which only
                     delivers to your own Resend account address until a domain is verified.

Exit code is non-zero only if a send was attempted and failed, so a delivery problem
surfaces as a failed run while leaving the unsent digest to retry next time.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

API_URL = "https://api.resend.com/emails"
DEFAULT_FROM = "TanStack Release Monitor <onboarding@resend.dev>"
HERE = Path(__file__).resolve().parent


def _subject(digest_text, fallback):
    """Use the digest's top-level heading as the subject; fall back to the file stem."""
    for line in digest_text.splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip()
    return fallback


def send_email(api_key, sender, to, subject, text):
    payload = {"from": sender, "to": [to], "subject": subject, "text": text}
    req = urllib.request.Request(
        API_URL, data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json",
                 # Resend requires a User-Agent; without one Cloudflare's Browser
                 # Integrity Check rejects the request with 403 (error code 1010).
                 "User-Agent": "tanstack-release-monitor"}, method="POST")
    with urllib.request.urlopen(req) as resp:
        body = json.loads(resp.read())
        return body.get("id") or (body.get("data") or {}).get("id", "")


def deliver(root):
    """Email every unsent digest. Returns (sent_count, failed_count)."""
    digest_dir = root / "digest"
    pending = ([p for p in sorted(digest_dir.glob("*.md"))
                if not (root / "sent" / p.name).exists()]
               if digest_dir.exists() else [])
    if not pending:
        print("no unsent digests")
        return 0, 0

    api_key = os.environ.get("RESEND_API_KEY", "")
    to = os.environ.get("DIGEST_EMAIL_TO", "")
    sender = os.environ.get("DIGEST_EMAIL_FROM", "") or DEFAULT_FROM
    if not api_key or not to:
        print(f"email not configured (RESEND_API_KEY/DIGEST_EMAIL_TO unset); "
              f"{len(pending)} digest(s) left unsent for a later run", file=sys.stderr)
        return 0, 0

    sent_dir = root / "sent"
    sent_dir.mkdir(parents=True, exist_ok=True)
    sent_n, failed_n = 0, 0
    for p in pending:
        text = p.read_text()
        try:
            mid = send_email(api_key, sender, to, _subject(text, p.stem), text)
            (sent_dir / p.name).write_text(f"sent id={mid}\n")
            print(f"sent {p.name} (id={mid})")
            sent_n += 1
        except urllib.error.HTTPError as e:
            raw = e.read().decode(errors="replace")
            try:
                err = json.loads(raw).get("error") or {}
                detail = f"{err.get('name')}: {err.get('message')}"
            except Exception:
                detail = raw[:400] or e.reason
            print(f"FAILED {p.name}: HTTP {e.code} {detail}", file=sys.stderr)
            failed_n += 1
        except (urllib.error.URLError, OSError, ValueError) as e:
            print(f"FAILED {p.name}: {e}", file=sys.stderr)
            failed_n += 1
    return sent_n, failed_n


def main():
    ap = argparse.ArgumentParser(description="Email unsent digests via Resend (stdlib).")
    ap.add_argument("--root", default=str(HERE.parent),
                    help="repo root containing digest/ and sent/")
    args = ap.parse_args()
    sent_n, failed_n = deliver(Path(args.root))
    print(f"email: {sent_n} sent, {failed_n} failed")
    if failed_n:
        sys.exit(1)  # surface as a failed run; unsent digests retry next time


if __name__ == "__main__":
    main()
