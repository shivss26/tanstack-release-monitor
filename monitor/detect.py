#!/usr/bin/env python3
"""Layer 1 - deterministic TanStack release detector.

Polls each configured repo's GitHub Releases and writes lossless records + state
back into this repo. No email, no agent. Three per-source styles (config `style`):

  * "rollup" (default)  - `release-YYYY-MM-DD-HHMM` rollup tags (router, query,
    create-tsrouter-app). Also tracks yanks (deleted rollups).
  * "single-package"    - plain stable `vX.Y.Z` tags from a one-package repo
    (intent). Also tracks yanks.
  * "package-batch"     - changesets-style repos that publish one GitHub release
    per package (`@tanstack/react-form@1.33.1`, ...). New stable releases are
    grouped into ONE batch record per publish, filtered to React-relevant
    packages (unprefixed core packages + `react-*`; other framework adapters are
    dropped). A settle delay makes sure a publish batch is complete before it is
    consumed, so one publish never splits across two runs. No yank tracking.

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
VERSION_TAG_RE = re.compile(r"^v\d+\.\d+\.\d+$")                      # stable only
PKG_TAG_RE = re.compile(r"^@tanstack/(?P<pkg>[a-z0-9-]+)@\d+\.\d+\.\d+$")  # stable only
# Framework adapters that are NOT part of the monitored (React) stack. A package
# whose first name token is one of these is dropped; unprefixed (core/shared)
# packages and `react-*` packages are kept.
OTHER_FRAMEWORKS = {"vue", "svelte", "solid", "preact", "lit", "angular", "marko", "qwik"}
# A publish batch clusters in ~1 min; wait until it's complete. Env-overridable
# so a testbench can shrink the wait; production leaves it at 30.
BATCH_SETTLE_MIN = int(os.environ.get("BATCH_SETTLE_MIN", "30"))
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


def make_matcher(tag_re):
    """Stable-release matcher for a tag regex (drafts + prereleases excluded)."""
    def match(r):
        return bool(
            tag_re.match(r.get("tag_name", "") or "")
            and not r.get("draft", False)
            and not r.get("prerelease", False)
        )
    return match


def fetch_matching(owner, repo, token, stop_at_id, match):
    """In-scope releases, newest-first, paging until we pass the watermark."""
    found = []
    for page in range(1, MAX_PAGES + 1):
        url = f"{API}/repos/{owner}/{repo}/releases?per_page={PER_PAGE}&page={page}"
        batch = api_json(url, token)
        if not batch:
            break
        found.extend(r for r in batch if match(r))
        min_id = min((r["id"] for r in batch), default=0)
        if len(batch) < PER_PAGE or min_id <= stop_at_id:
            break
    return found


def react_relevant(tag_name):
    """True if a `@tanstack/<pkg>@x.y.z` package belongs to the React stack.

    Kept: unprefixed core/shared/provider packages and `react-*` packages.
    Dropped: any package whose name contains an other-framework token — checked
    against EVERY token because some repos suffix the framework (`ai-vue`,
    `solid-ai-devtools`), not just prefix it (`vue-form`).
    """
    m = PKG_TAG_RE.match(tag_name or "")
    if not m:
        return False
    return not any(t in OTHER_FRAMEWORKS for t in m["pkg"].split("-"))


# --- output writers ----------------------------------------------------------

def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def det_stamp_now():
    # Detection time in IST, `YYYY-MM-DD-HHMM`. Computed once per run so multiple
    # rollups detected together share it; the tag disambiguates. The orchestrator
    # keys raw/prefetch/summary on this stamp.
    return datetime.now(IST).strftime("%Y-%m-%d-%H%M")


def write_raw(label, record, det_stamp, tag):
    d = ROOT / "raw" / label
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{det_stamp}__{tag}.json"
    path.write_text(json.dumps(record, indent=2) + "\n")
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

def process_watched(src, state, det_stamp, tag_re, wrap=None):
    """Shared flow for the "rollup" and "single-package" styles: one release =
    one raw record, with yank tracking. `wrap` optionally wraps the raw release
    JSON in a style envelope for the enrich stage."""
    label, owner, repo = src["label"], src["owner"], src["repo"]
    token = os.environ.get("GITHUB_TOKEN", "")
    detected_at = now_iso()
    st = state.get(label)
    match = make_matcher(tag_re)

    # First time we see a source: establish the watermark + yank-watch, emit nothing.
    # (Prevents flooding on the initial run against a repo with existing history.)
    if st is None:
        releases = fetch_matching(owner, repo, token, 0, match)
        max_id = max((r["id"] for r in releases), default=0)
        yank_watch = {
            str(r["id"]): {"tag": r["tag_name"], "published_at": r.get("published_at")}
            for r in releases
        }
        state[label] = {"last_seen_id": max_id, "yank_watch": yank_watch}
        print(f"[{label}] baseline: last_seen_id={max_id}, "
              f"{len(yank_watch)} release(s) watched, nothing emitted")
        return

    last_seen_id = st.get("last_seen_id", 0)
    yank_watch = dict(st.get("yank_watch", {}))
    releases = fetch_matching(owner, repo, token, last_seen_id, match)
    current_ids = {r["id"] for r in releases}

    # 1) New releases (id above the watermark), oldest first.
    added = sorted((r for r in releases if r["id"] > last_seen_id), key=lambda r: r["id"])
    for r in added:
        record = wrap(r) if wrap else r
        path = write_raw(label, record, det_stamp, r["tag_name"])
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
        print(f"[{label}] no new releases")


def _parse_pub(r):
    try:
        return datetime.strptime(r.get("published_at") or "",
                                 "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def process_batch(src, state, det_stamp):
    """"package-batch" style: group new stable per-package releases into ONE raw
    record, keeping only React-relevant packages. No yank tracking (per-package
    GitHub releases are effectively never yanked; npm unpublish isn't visible
    here anyway).

    Settle rule: a release younger than BATCH_SETTLE_MIN is left for the next
    run, and nothing later than the oldest unsettled release is consumed either,
    so a publish batch is always taken whole. If several distinct publishes
    settle between two runs they are grouped into one record — fine, the digest
    just covers both.
    """
    label, owner, repo = src["label"], src["owner"], src["repo"]
    token = os.environ.get("GITHUB_TOKEN", "")
    st = state.get(label)
    match = make_matcher(PKG_TAG_RE)

    if st is None:
        cands = fetch_matching(owner, repo, token, 0, match)
        max_id = max((r["id"] for r in cands), default=0)
        state[label] = {"last_seen_id": max_id}
        print(f"[{label}] baseline: last_seen_id={max_id}, nothing emitted")
        return

    last_seen_id = st.get("last_seen_id", 0)
    cands = fetch_matching(owner, repo, token, last_seen_id, match)
    new = sorted((r for r in cands if r["id"] > last_seen_id), key=lambda r: r["id"])
    if not new:
        print(f"[{label}] no new releases")
        return

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=BATCH_SETTLE_MIN)
    unsettled = []
    for r in new:
        pub = _parse_pub(r)
        if pub is None or pub > cutoff:
            unsettled.append(r["id"])
    ceiling = min(unsettled) - 1 if unsettled else None
    consume = [r for r in new if ceiling is None or r["id"] <= ceiling]
    if not consume:
        print(f"[{label}] {len(new)} release(s) still settling (<{BATCH_SETTLE_MIN}m old); "
              f"deferred to next run")
        return

    included = [r for r in consume if react_relevant(r["tag_name"])]
    excluded = [r["tag_name"] for r in consume if not react_relevant(r["tag_name"])]
    state[label] = {"last_seen_id": max(r["id"] for r in consume)}

    if not included:
        print(f"[{label}] {len(excluded)} non-React release(s) skipped, nothing emitted")
        return

    newest = max(d for d in (_parse_pub(r) for r in included) if d is not None)
    batch_tag = "batch-" + newest.strftime("%Y-%m-%d-%H%M")
    record = {
        "style": "package-batch",
        "tag": batch_tag,
        "published_at": newest.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "releases": [{"id": r["id"], "tag_name": r["tag_name"],
                      "published_at": r.get("published_at"),
                      "html_url": r.get("html_url", ""),
                      "body": r.get("body") or ""} for r in included],
        "excluded_tags": excluded,
    }
    path = write_raw(label, record, det_stamp, batch_tag)
    print(f"[{label}] NEW {batch_tag}: {len(included)} React-relevant release(s), "
          f"{len(excluded)} other-framework release(s) filtered -> {path.relative_to(ROOT)}")


def process_source(src, state, det_stamp):
    style = src.get("style", "rollup")
    if style == "rollup":
        tag_re = re.compile(src["tag_re"]) if src.get("tag_re") else ROLLUP_RE
        process_watched(src, state, det_stamp, tag_re)
    elif style == "single-package":
        process_watched(src, state, det_stamp, VERSION_TAG_RE,
                        wrap=lambda r: {"style": "single-package", "release": r})
    elif style == "package-batch":
        process_batch(src, state, det_stamp)
    else:
        raise ValueError(f"unknown style {style!r} for source {src.get('label')}")


def main():
    config = json.loads(CONFIG_PATH.read_text())
    state = json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else {}
    det_stamp = det_stamp_now()  # one stamp for the whole run
    for src in config["sources"]:
        process_source(src, state, det_stamp)
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n")


if __name__ == "__main__":
    main()
