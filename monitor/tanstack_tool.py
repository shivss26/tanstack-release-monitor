#!/usr/bin/env python3
"""Custom client-side doc tools backed by the pinned `@tanstack/cli` (v0.69.5).

Exposes two Anthropic tool definitions + a dispatcher the harness calls when the
model emits a `tool_use` for them. The CLI runs in OUR process (full network),
which is why doc access works here even though Skills can't reach the network in
the Messages-API container.

Verified command shapes (Stage C, against @tanstack/cli@0.69.3):
  * `tanstack libraries --json`                -> {libraries:[{id,...}]}
  * `tanstack search-docs <q> --library <id> --json`
                                               -> {query,totalHits,results:[{title,url,snippet,breadcrumb}]}
  * `tanstack doc <id> <path> --json`          -> {title,content,url,library,version}
"""
import json
import os
import re
import shutil
import subprocess

# Valid library ids (from `tanstack libraries --json`). The doc tools constrain
# their `library` param to exactly this set so the model can't invent an id.
LIBRARY_IDS = [
    "start", "router", "query", "table", "form", "db", "ai", "intent",
    "virtual", "pacer", "hotkeys", "store", "ranger", "config", "devtools",
    "cli", "workflow",
]

_TIMEOUT = 60
_MAX_OUTPUT = 25 * 1024  # cap each tool_result so a huge page can't blow context
_TAG_RE = re.compile(r"<[^>]+>")

# --- binary resolution -------------------------------------------------------

_BIN_CACHE = None


def _tanstack_bin():
    """Locate the `tanstack` binary: PATH first, then pnpm's global bin dir."""
    global _BIN_CACHE
    if _BIN_CACHE:
        return _BIN_CACHE
    found = shutil.which("tanstack")
    if not found:
        try:
            gbin = subprocess.run(["pnpm", "-g", "bin"], capture_output=True,
                                  text=True, timeout=15).stdout.strip()
            cand = os.path.join(gbin, "tanstack")
            if gbin and os.path.exists(cand):
                found = cand
        except (OSError, subprocess.SubprocessError):
            found = None
    if not found:
        raise RuntimeError(
            "`tanstack` CLI not found. Install it: pnpm add -g @tanstack/cli@0.69.5")
    _BIN_CACHE = found
    return found


def _run(args):
    proc = subprocess.run([_tanstack_bin(), *args], capture_output=True,
                          text=True, timeout=_TIMEOUT)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "").strip()
                           or f"tanstack {' '.join(args)} exited {proc.returncode}")
    return proc.stdout


def _clean(s):
    return _TAG_RE.sub("", s or "").strip()


# --- tool implementations ----------------------------------------------------

def doc_search(library, query):
    data = json.loads(_run(["search-docs", query, "--library", library, "--json"]))
    results = data.get("results", [])
    if not results:
        return f'No documentation hits for "{query}" in library "{library}".'
    lines = [f'Search results for "{query}" in {library} '
             f'({data.get("totalHits", len(results))} hits):', ""]
    for r in results[:10]:
        crumb = " > ".join(r.get("breadcrumb", []))
        lines.append(f"- {r.get('title', '')}")
        if crumb:
            lines.append(f"  path: {crumb}")
        lines.append(f"  url: {r.get('url', '')}")
        snip = _clean(r.get("snippet"))
        if snip:
            lines.append(f"  snippet: {snip}")
    return "\n".join(lines)[:_MAX_OUTPUT]


def doc_get(library, path):
    path = path.strip()
    # Drop any URL fragment (#section) or query (?...): an anchor only points within
    # a page, so passing it through makes the CLI look up a page that doesn't exist.
    path = path.split("#", 1)[0].split("?", 1)[0].strip()
    # Accept a full docs URL or a bare path; the CLI wants the path after /docs/.
    m = re.search(r"/docs/(.+?)/?$", path)
    if m:
        path = m.group(1)
    path = path.rstrip("/")
    data = json.loads(_run(["doc", library, path, "--docs-version", "latest", "--json"]))
    out = (f"# {data.get('title', '')}\n"
           f"({data.get('library', library)} {data.get('version', '')}) "
           f"{data.get('url', '')}\n\n{data.get('content', '')}")
    return out.strip()[:_MAX_OUTPUT]


# --- Anthropic tool definitions ----------------------------------------------

TOOLS = [
    {
        "name": "tanstack_doc_search",
        "description": (
            "Search the OFFICIAL, version-current TanStack documentation for a library. "
            "Call this whenever a PR changes a public API, option, default, or behavior and "
            "the exact old->new behavior is not fully spelled out in the PR/issue text — do "
            "NOT infer TanStack API details from prior knowledge; verify against the docs. "
            "Pass the library id given in the change's [brackets]. Returns matching doc "
            "pages with titles, URLs, and snippets; follow up with tanstack_doc_get for full text."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "library": {
                    "type": "string",
                    "enum": LIBRARY_IDS,
                    "description": "TanStack library id (the [bracketed] tag on the change).",
                },
                "query": {
                    "type": "string",
                    "description": "What to search for (API name, option, concept).",
                },
            },
            "required": ["library", "query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "tanstack_doc_get",
        "description": (
            "Fetch the FULL text of one TanStack documentation page (after locating it with "
            "tanstack_doc_search). Use when a snippet is not enough to state the old vs. new "
            "behavior precisely. Accepts the page's doc path (e.g. 'api/router/"
            "retainSearchParamsFunction' or 'framework/react/guide/data-loading') or its full "
            "tanstack.com docs URL."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "library": {
                    "type": "string",
                    "enum": LIBRARY_IDS,
                    "description": "TanStack library id.",
                },
                "path": {
                    "type": "string",
                    "description": "Doc path after /docs/, or the full docs URL.",
                },
            },
            "required": ["library", "path"],
            "additionalProperties": False,
        },
    },
]

TOOL_NAMES = {t["name"] for t in TOOLS}


def dispatch(name, tool_input):
    """Run a custom tool. Returns (content_str, is_error)."""
    try:
        if name == "tanstack_doc_search":
            return doc_search(tool_input["library"], tool_input["query"]), False
        if name == "tanstack_doc_get":
            return doc_get(tool_input["library"], tool_input["path"]), False
        return f"Unknown tool: {name}", True
    except subprocess.TimeoutExpired:
        return f"`tanstack` timed out after {_TIMEOUT}s.", True
    except (RuntimeError, json.JSONDecodeError, KeyError, OSError) as e:
        return f"Doc tool error ({name}): {e}", True


if __name__ == "__main__":
    # Manual smoke test (no API key needed).
    print(doc_search("router", "retain search params"))
    print("\n---\n")
    print(doc_get("router", "api/router/retainSearchParamsFunction")[:500])
