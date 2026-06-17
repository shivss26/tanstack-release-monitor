#!/usr/bin/env python3
"""Layer 3 prefetch — deterministic rollup -> enrichment context (simplified).

The script does ONLY the work that is unambiguous and reliable. Everything that
needs judgement — which library a change belongs to, what's worth summarising,
old-vs-new behaviour — is left to the enrichment agent, which has the PR bodies,
the repo's candidate libraries, and the doc/web tools.

Deterministic output:
  * the repo and the libraries that repo covers (router -> router + start; every
    other repo -> its own library),
  * the FULL rollup release notes, VERBATIM (nothing is dropped or rewritten),
  * the body + linked issue of each NON-NOISE pull request, fetched by number.

"Noise" (the `### Chore` category + monorepo-infra scopes — see dropped-categories.md)
only suppresses PR-body *fetching*; every change still appears in the verbatim notes,
so nothing is hidden from the agent.

If the body isn't the expected Changesets format, the raw text is passed through with
a warning to the agent and nothing is fetched (meta.format_matched = False).

Stdlib only (urllib + json). Reads GITHUB_TOKEN from env (optional for public repos).
Mirrors monitor/detect.py's request pattern.
"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.github.com"

# Noise = a change whose PR body won't describe a library behaviour change, so we
# don't spend an API call fetching it. Shared across ALL TanStack repos — the
# `### Chore` category is universal, and none of these infra scopes collide with a
# TanStack library id. See dropped-categories.md for the full rationale.
INFRA_SCOPES = {
    "examples", "example", "test", "tests", "benchmark", "benchmarks", "bench",
    "e2e", "ci", "docs", "doc", "build", "scripts", "script",
}

# Sentinel for an empty / dependency-only rollup — a legit `## Changes` body, not
# a format mismatch.
EMPTY_SENTINEL_RE = re.compile(r"^[-*]\s+no\s+(changelog\s+entries|changes)\b", re.I)

# `- <scope>: <desc> (#1234) (abc1234) by @author`  — scope/author optional.
# Author may be a bot handle like `renovate[bot]` / `dependabot[bot]`.
CHANGE_RE = re.compile(
    r"^-\s+(?P<body>.*?)\s+\(#(?P<pr>\d+)\)\s+\((?P<sha>[0-9a-f]{6,40})\)"
    r"(?:\s+by\s+@(?P<author>[A-Za-z0-9-]+(?:\[bot\])?))?\s*$"
)
# Safety net: any `## Changes` bullet carrying a (#NNNN) that the strict shape
# misses is still recovered via this, so a PR can never be silently dropped.
LOOSE_PR_RE = re.compile(r"\(#(\d+)\)")
HEADER_RE = re.compile(r"^###\s+(?P<cat>.+?)\s*$")
FIXES_RE = re.compile(r"(?:fix(?:e[sd])?|close[sd]?|resolve[sd]?)\s+#(\d+)", re.I)


# --- GitHub API helpers (mirrors detect.py) ----------------------------------

def _request(url, token, accept="application/vnd.github+json"):
    req = urllib.request.Request(url)
    req.add_header("Accept", accept)
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "tanstack-release-monitor")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    return req


def api_json(url, token):
    with urllib.request.urlopen(_request(url, token)) as resp:
        return json.loads(resp.read().decode())


# --- repo -> covered libraries -----------------------------------------------

def default_libs(repo):
    """Libraries a repo covers when no explicit allow-list is configured.

    router publishes both Router and Start; every other repo maps to its own name.
    """
    r = (repo or "").lower()
    return ["router", "start"] if r == "router" else [r]


# --- noise + change parsing --------------------------------------------------

def _tokens(s):
    return [t for t in re.split(r"[-/_.\s]+", s.lower()) if t]


def is_noise(category, scope):
    """True if the PR body is not worth fetching (chore / infra-scoped change)."""
    first = (_tokens(scope) or [""])[0]
    return (category or "").lower() == "chore" or first in INFRA_SCOPES or scope.startswith(".")


def analyze_format(body):
    """Cheap structural probe: (has `## Changes` header, # of MEANINGFUL bullets).

    "Meaningful" excludes the `- No changelog entries` sentinel, so an empty
    dependency-only rollup reads as well-formed (0 bullets), not as format drift.
    Counts both `-` and `*` bullets so a non-Changesets changelog style still
    trips the mismatch path.
    """
    has_header, n_bullets, in_changes = False, 0, False
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("## "):
            in_changes = s.lower().startswith("## changes")
            if in_changes:
                has_header = True
            continue
        if in_changes and (s.startswith("- ") or s.startswith("* ")):
            if not EMPTY_SENTINEL_RE.match(s):
                n_bullets += 1
    return has_header, n_bullets


def _make_change(pr, cbody, category, author="", loose=False):
    """Build one change record from a `## Changes` bullet body (text before (#NNNN))."""
    cbody = cbody.strip()
    if ": " in cbody:
        scope, desc = cbody.split(": ", 1)
    else:
        scope, desc = "", cbody
    scope, desc = scope.strip(), desc.strip()
    return {"pr": pr, "scope": scope, "description": desc,
            "category": (category or "other"), "author": author,
            "noise": is_noise(category, scope), "loose": loose}


def parse_changes(body):
    """Parse `## Changes` -> list of change records (in document order).

    No library inference — that's the agent's job. A bullet carrying a (#NNNN)
    that fails the strict CHANGE_RE is still recovered via LOOSE_PR_RE so no PR is
    ever silently lost.
    """
    changes, category, in_changes = [], None, False
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("## "):
            in_changes = s.lower().startswith("## changes")
            category = None
            continue
        if not in_changes:
            continue
        h = HEADER_RE.match(s)
        if h:
            category = h.group("cat").strip().lower()
            continue
        m = CHANGE_RE.match(s)
        if m:
            changes.append(_make_change(int(m.group("pr")), m.group("body"),
                                        category, m.group("author") or ""))
            continue
        loose = LOOSE_PR_RE.search(s)
        if s.startswith("- ") and loose:
            pr = int(loose.group(1))
            print(f"  [loose-parse] recovered #{pr} from non-standard change line: "
                  f"{s!r}", file=sys.stderr)
            changes.append(_make_change(pr, s[2:].split(" (#")[0], category, loose=True))
    return changes


# --- PR / issue fetch --------------------------------------------------------

def fetch_pr(owner, repo, pr, token):
    """PR title/body/author + linked issue (no diff — the agent uses doc/web tools)."""
    data = api_json(f"{API}/repos/{owner}/{repo}/issues/{pr}", token)
    body = data.get("body") or ""
    out = {
        "pr": pr,
        "pr_title": data.get("title") or "",
        "pr_author": (data.get("user") or {}).get("login") or "",
        "pr_body": body.strip(),
        "linked_issue": None,
    }
    fm = FIXES_RE.search(body)
    if fm:
        try:
            issue = api_json(f"{API}/repos/{owner}/{repo}/issues/{fm.group(1)}", token)
            out["linked_issue"] = {
                "number": int(fm.group(1)),
                "title": issue.get("title") or "",
                "body": (issue.get("body") or "").strip(),
            }
        except urllib.error.HTTPError:
            pass  # linked number may be a PR/cross-repo ref; skip quietly
    return out


# --- context rendering -------------------------------------------------------

def render_context(meta, body, changes, details):
    L = [f"# Release rollup: {meta['tag']}", "",
         f"- Repository: `{meta['owner']}/{meta['repo']}`",
         f"- Libraries this repo covers: {', '.join(meta['libraries'])}", "",
         "Below are the COMPLETE release notes verbatim, then the full body of each "
         "substantive pull request. You decide which changes matter and which library "
         "(one of those above) each belongs to; chore/test/example/CI changes can be "
         "mentioned briefly or skipped. Use the doc/web tools for old-vs-new behaviour.",
         "", "---", "", "## Release notes (verbatim)", "", body.strip(), "", "---", ""]

    if any(c["pr"] in details for c in changes):
        L += ["## Pull request details", "",
              "(Noise PRs — chore/test/example/CI — are omitted here but remain in the "
              "verbatim notes above.)", ""]
        seen = set()
        for c in changes:
            d = details.get(c["pr"])
            if not d or c["pr"] in seen:
                continue
            seen.add(c["pr"])
            scope = f"{c['scope']}: " if c["scope"] else ""
            by = f" — by @{d['pr_author']}" if d.get("pr_author") else ""
            L.append(f"### #{c['pr']} — {scope}{c['description']}")
            L.append(f"- category: {c['category']}{by}")
            if d.get("linked_issue"):
                L.append(f"- fixes issue #{d['linked_issue']['number']}: {d['linked_issue']['title']}")
            L.append("")
            if d.get("pr_body"):
                L += ["**PR description:**", "", d["pr_body"], ""]
            if d.get("linked_issue") and d["linked_issue"].get("body"):
                L += [f"**Linked issue #{d['linked_issue']['number']} description:**", "",
                      d["linked_issue"]["body"], ""]
            L += ["---", ""]
    return "\n".join(L)


def _fallback_context(meta, body, reason):
    """Raw rollup + a loud warning, when the body isn't our Changesets format."""
    return (
        f"# Release rollup: {meta['tag']}  ⚠️ UNRECOGNISED FORMAT\n\n"
        f"- Repository: `{meta['owner']}/{meta['repo']}`\n"
        f"- Libraries this repo covers: {', '.join(meta['libraries'])}\n\n"
        f"> WARNING TO THE AGENT: this rollup did NOT match the expected TanStack "
        f"Changesets format ({reason}). The script could not identify pull requests, so "
        f"no PR bodies were fetched. Summarise directly from the raw release notes below "
        f"and use the doc/web tools as needed. A human has been flagged to check it.\n\n"
        f"---\n\n## Release notes (verbatim)\n\n{body.strip()}\n"
    )


def assemble(owner, repo, tag, body, html_url="", token="", source_libs=None):
    """Body-in context assembler (no release fetch). Reused by the runner + tests.

    `source_libs`: the libraries this source covers (e.g. router -> {router, start}).
    Surfaced to the agent as the candidate set; no per-change library inference here.
    On a body that isn't our format, returns the raw fallback (format_matched=False).
    """
    libs = sorted(source_libs) if source_libs else default_libs(repo)
    meta = {"tag": tag, "owner": owner, "repo": repo, "html_url": html_url or "",
            "libraries": libs, "format_matched": True}

    has_header, n_bullets = analyze_format(body)
    changes = parse_changes(body)
    if not has_header or (n_bullets > 0 and not changes):
        meta["format_matched"] = False
        reason = ("no `## Changes` section" if not has_header
                  else f"{n_bullets} change line(s) but none matched the expected shape")
        meta["warning"] = reason
        return _fallback_context(meta, body, reason), {
            "meta": meta, "changes": [], "warning": reason}

    # Fetch bodies for non-noise PRs only (deduped). Noise stays in verbatim notes.
    details, seen = {}, set()
    for c in changes:
        if c["noise"] or c["pr"] in seen:
            continue
        seen.add(c["pr"])
        details[c["pr"]] = fetch_pr(owner, repo, c["pr"], token)

    context_md = render_context(meta, body, changes, details)
    inscope = {
        "meta": meta,
        "changes": [
            {"pr": c["pr"], "scope": c["scope"], "category": c["category"],
             "title": c["description"], "noise": c["noise"],
             "fetched": c["pr"] in details}
            for c in changes
        ],
    }
    return context_md, inscope


def build(owner, repo, tag, token, source_libs=None):
    rel = api_json(f"{API}/repos/{owner}/{repo}/releases/tags/{tag}", token)
    return assemble(owner, repo, tag, rel.get("body") or "",
                    rel.get("html_url") or "", token, source_libs=source_libs)


def main():
    ap = argparse.ArgumentParser(description="Assemble enrichment context for a rollup.")
    ap.add_argument("--owner", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--out-dir", default=str(Path(__file__).resolve().parent / "samples"))
    ap.add_argument("--libraries", default="",
                    help="comma-separated libraries this repo covers (default: derived from repo)")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN", "")
    source_libs = {s.strip() for s in args.libraries.split(",") if s.strip()} or None
    context_md, inscope = build(args.owner, args.repo, args.tag, token, source_libs=source_libs)

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{args.tag}.context.md").write_text(context_md)
    (out / f"{args.tag}.inscope.json").write_text(json.dumps(inscope, indent=2) + "\n")

    chs = inscope["changes"]
    n_fetched = sum(1 for c in chs if c.get("fetched"))
    if not inscope["meta"].get("format_matched", True):
        print(f"[{args.tag}] ⚠️ FORMAT MISMATCH ({inscope.get('warning')}) "
              f"— raw fallback emitted, human attention needed", file=sys.stderr)
    print(f"[{args.tag}] libraries={inscope['meta']['libraries']} "
          f"changes={len(chs)} fetched={n_fetched} noise={sum(1 for c in chs if c['noise'])}")
    print(f"  -> {out / (args.tag + '.context.md')}")
    print(f"  -> {out / (args.tag + '.inscope.json')}")


if __name__ == "__main__":
    main()
