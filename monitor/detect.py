#!/usr/bin/env python3
"""Layer 1 - deterministic TanStack release-rollup detector.

Polls each configured repo's GitHub Releases, detects new `release-*` rollups and
confirmed yanks, and writes lossless records + state back into this repo. No email,
no agent. See LAYER-1-PLAN.md in the design folder.

Stdlib only (urllib + json) so the runner needs no dependency install.
Reads GITHUB_TOKEN from env to raise the API rate limit (and is optional for public
repos). Run from anywhere: paths are resolved relative to this file.
"""
import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

API = "https://api.github.com"
ROLLUP_RE = re.compile(r"^release-\d{4}-\d{2}-\d{2}-\d{4}$")
PER_PAGE = 100
MAX_PAGES = 10
YANK_WATCH_DAYS = 30
IST = timezone(timedelta(hours=5, minutes=30))  # fixed offset; no tzdata dependency

ROOT = Path(__file__).resolve().parent.parent  # monitor/ -> repo root
STATE_PATH = ROOT / "state.json"
CONFIG_PATH = ROOT / "monitor" / "config.json"


# --- GitHub API helpers ------------------------------------------------------

def _request(url, token):
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "tanstack-release-monitor")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    return req


def api_json(url, token):
    with urllib.request.urlopen(_request(url, token)) as resp:
        return json.loads(resp.read().decode())


def release_exists(owner, repo, release_id, token):
    """True if the release id still exists (HTTP 200); False if deleted (404)."""
    url = f"{API}/repos/{owner}/{repo}/releases/{release_id}"
    try:
        urllib.request.urlopen(_request(url, token))
        return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        raise


def is_rollup(r):
    return bool(
        ROLLUP_RE.match(r.get("tag_name", "") or "")
        and not r.get("draft", False)
        and not r.get("prerelease", False)
    )


def fetch_rollups(owner, repo, token, stop_at_id=0):
    """In-scope rollups, newest-first, paging until we pass the watermark."""
    rollups = []
    for page in range(1, MAX_PAGES + 1):
        url = f"{API}/repos/{owner}/{repo}/releases?per_page={PER_PAGE}&page={page}"
        batch = api_json(url, token)
        if not batch:
            break
        rollups.extend(r for r in batch if is_rollup(r))
        min_id = min((r["id"] for r in batch), default=0)
        if len(batch) < PER_PAGE or min_id <= stop_at_id:
            break
    return rollups


# --- output writers ----------------------------------------------------------

def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def det_stamp_now():
    # Detection time in IST, `YYYY-MM-DD-HHMM`. Computed once per run so multiple
    # rollups detected together share it; the tag disambiguates. The orchestrator
    # keys raw/prefetch/summary on this stamp.
    return datetime.now(IST).strftime("%Y-%m-%d-%H%M")


def write_raw(label, r, det_stamp):
    d = ROOT / "raw" / label
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{det_stamp}__{r['tag_name']}.json"
    path.write_text(json.dumps(r, indent=2) + "\n")
    return path


def write_yank(label, entry, det_stamp, detected_at):
    d = ROOT / "yanks" / label
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{det_stamp}__{entry['tag']}.md"
    path.write_text(
        f"# YANK detected: {entry['tag']}\n\n"
        f"- release id: {entry['id']}\n"
        f"- tag: {entry['tag']}\n"
        f"- originally published: {entry.get('published_at', 'unknown')}\n"
        f"- yank detected at: {detected_at}\n\n"
        f"The GitHub release for this rollup returned 404 (deleted / unpublished).\n"
    )
    return path


# --- core --------------------------------------------------------------------

def process_source(src, state, det_stamp):
    label, owner, repo = src["label"], src["owner"], src["repo"]
    token = os.environ.get("GITHUB_TOKEN", "")
    detected_at = now_iso()
    st = state.get(label)

    # First time we see a source: establish the watermark + yank-watch, emit nothing.
    # (Prevents flooding on the initial run against a repo with existing history.)
    if st is None:
        rollups = fetch_rollups(owner, repo, token, stop_at_id=0)
        max_id = max((r["id"] for r in rollups), default=0)
        yank_watch = {
            str(r["id"]): {"tag": r["tag_name"], "published_at": r.get("published_at")}
            for r in rollups
        }
        state[label] = {"last_seen_id": max_id, "yank_watch": yank_watch}
        print(f"[{label}] baseline: last_seen_id={max_id}, "
              f"{len(yank_watch)} rollup(s) watched, nothing emitted")
        return

    last_seen_id = st.get("last_seen_id", 0)
    yank_watch = dict(st.get("yank_watch", {}))
    rollups = fetch_rollups(owner, repo, token, stop_at_id=last_seen_id)
    current_ids = {r["id"] for r in rollups}

    # 1) New rollups (id above the watermark), oldest first.
    added = sorted((r for r in rollups if r["id"] > last_seen_id), key=lambda r: r["id"])
    for r in added:
        path = write_raw(label, r, det_stamp)
        yank_watch[str(r["id"])] = {"tag": r["tag_name"], "published_at": r.get("published_at")}
        last_seen_id = max(last_seen_id, r["id"])
        print(f"[{label}] NEW {r['tag_name']} (id={r['id']}) -> {path.relative_to(ROOT)}")

    # 2) Yank check: watched ids no longer present -> verify with a direct GET.
    for id_str in list(yank_watch.keys()):
        rid = int(id_str)
        if rid in current_ids:
            continue
        if not release_exists(owner, repo, rid, token):
            entry = {"id": rid, **yank_watch[id_str]}
            path = write_yank(label, entry, det_stamp, detected_at)
            del yank_watch[id_str]
            print(f"[{label}] YANK {entry['tag']} (id={rid}) -> {path.relative_to(ROOT)}")
        # HTTP 200 -> merely aged out of the fetch window; keep watching.

    # 3) Prune yank-watch entries older than YANK_WATCH_DAYS.
    cutoff = datetime.now(timezone.utc) - timedelta(days=YANK_WATCH_DAYS)
    for id_str in list(yank_watch.keys()):
        pub = yank_watch[id_str].get("published_at")
        if not pub:
            continue
        try:
            dt = datetime.strptime(pub, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if dt < cutoff:
            del yank_watch[id_str]

    state[label] = {"last_seen_id": last_seen_id, "yank_watch": yank_watch}
    if not added:
        print(f"[{label}] no new rollups")


def main():
    config = json.loads(CONFIG_PATH.read_text())
    state = json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else {}
    det_stamp = det_stamp_now()  # one stamp for the whole run
    for src in config["sources"]:
        process_source(src, state, det_stamp)
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n")


if __name__ == "__main__":
    main()
