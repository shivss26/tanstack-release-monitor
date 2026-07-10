#!/usr/bin/env python3
"""Layer 3 agent harness — turn one prefetch context into a human-readable summary.

Reads samples/<tag>.context.md + samples/<tag>.inscope.json (produced by
prefetch.py), runs the model in MODEL with the web search/fetch server tools, and
writes a final summary file named by the DETECTION timestamp (the stamp the raw
rollup and prefetch artifacts also share, so all three are findable together).

The script owns the deterministic header (repo, library, IST timestamps, file
list); the model writes only the plain-language bullet list.

  uv run python prefetch.py --owner TanStack --repo query --tag <tag> --libraries query
  uv run python enrich.py --tag <tag>
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import anthropic

ROOT = Path(__file__).resolve().parent
MODEL = "claude-sonnet-5"
MAX_TOKENS = 16000  # Sonnet 5's tokenizer spends ~30% more tokens for the same text
MAX_CONTINUATIONS = 10
IST = timezone(timedelta(hours=5, minutes=30))
TAG_DT_RE = re.compile(r"release-(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})")

SERVER_TOOLS = [
    {"type": "web_search_20260209", "name": "web_search"},
    {"type": "web_fetch_20260209", "name": "web_fetch"},
]
TASK = ("\n\n---\n\nWrite the change summary now as a markdown bullet list — one "
        "`- ` bullet per substantive change, plain language, no code. Output ONLY "
        "the bullets, no title or preamble.")


def _load_env():
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


# --- time helpers ------------------------------------------------------------

def tag_to_ist(tag):
    """`release-2026-06-02-1926` (UTC) -> aware IST datetime, or None."""
    m = TAG_DT_RE.search(tag or "")
    if not m:
        return None
    y, mo, d, h, mi = map(int, m.groups())
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc).astimezone(IST)


def fmt_ist(dt):
    return dt.strftime("%Y-%m-%d %H:%M IST") if dt else "unknown"


def stamp_ist(dt):
    return dt.strftime("%Y-%m-%d-%H%M")


# --- agent loop --------------------------------------------------------------

def _text(content):
    return next((b.text for b in content if b.type == "text"), None)


def run_agent(context_md, system, transcript, metrics):
    client = anthropic.Anthropic()
    messages = [{"role": "user", "content": context_md + TASK}]

    for i in range(MAX_CONTINUATIONS):
        metrics["iterations"] = i + 1
        with client.messages.stream(
            model=MODEL, max_tokens=MAX_TOKENS,
            system=system,
            # display="summarized" so the transcript log captures the reasoning
            # (Sonnet 5 defaults to omitted); billing is identical either way.
            thinking={"type": "adaptive", "display": "summarized"},
            output_config={"effort": "medium"},
            tools=SERVER_TOOLS, messages=messages,
        ) as stream:
            resp = stream.get_final_message()

        transcript.append(f"\n=== iteration {i + 1} (stop={resp.stop_reason}) ===")
        for b in resp.content:
            if b.type == "thinking" and (getattr(b, "thinking", "") or "").strip():
                transcript.append(f"[thinking]\n{b.thinking.strip()}")
            elif b.type == "server_tool_use":
                metrics["web_calls"] += 1
                transcript.append(f"[server_tool_use] {b.name}({json.dumps(b.input)})")

        # Web search/fetch run server-side; a long tool turn surfaces as pause_turn.
        if resp.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": resp.content})
            continue
        if resp.stop_reason == "end_turn":
            return (_text(resp.content) or "").strip()
        # refusal / max_tokens / other -> return whatever text we have
        transcript.append(f"[halt] stop_reason={resp.stop_reason}")
        return (_text(resp.content) or "").strip()
    return ""


# --- summary assembly --------------------------------------------------------

def build_summary(meta, bullets, noise_count, detected_ist, stamp, label=""):
    repo = f"{meta['owner']}/{meta['repo']}"
    libs = ", ".join(meta.get("libraries", [])) or meta.get("repo", "")
    released = tag_to_ist(meta.get("tag", ""))
    if released is None and meta.get("published_at"):
        try:
            released = datetime.strptime(
                meta["published_at"], "%Y-%m-%dT%H:%M:%SZ"
            ).replace(tzinfo=timezone.utc).astimezone(IST)
        except ValueError:
            pass
    L = ["# TanStack release summary", "",
         f"- Repository: {repo}",
         f"- Library: {libs}",
         f"- Release: {meta.get('tag', '')}",
         f"- Released: {fmt_ist(released)}",
         f"- Detected by monitor: {fmt_ist(detected_ist)}"]
    if meta.get("html_url"):
        L.append(f"- Link: {meta['html_url']}")
    if meta.get("packages"):
        L.append(f"- Packages: {', '.join(meta['packages'])}")
    L.append("")
    if not meta.get("format_matched", True):
        L += ["> ⚠️ This rollup used an UNRECOGNISED format; the prefetch script could not "
              "parse its pull requests. The summary below is based on the raw release notes "
              "only and may be incomplete.", ""]
    L += ["## Changes", "", bullets or "_(no summary produced)_"]
    if noise_count:
        L.append(f"- _Plus {noise_count} chore / dependency / other-framework / test "
                 f"update{'s' if noise_count != 1 else ''}, not detailed._")
    if meta.get("excluded_count"):
        L.append(f"- _{meta['excluded_count']} non-React framework package "
                 f"release{'s' if meta['excluded_count'] != 1 else ''} filtered out._")
    seg = f"{label}/" if label else ""
    L += ["", "## Files (this detection)",
          f"- raw rollup: `raw/{seg}{stamp}.json`",
          f"- prefetch:   `prefetch/{seg}{stamp}.md`",
          f"- summary:    `summaries/{seg}{stamp}.md`", ""]
    return "\n".join(L)


def run(tag, detected_at, samples_dir, out_dir):
    context_md = (samples_dir / f"{tag}.context.md").read_text()
    inscope = json.loads((samples_dir / f"{tag}.inscope.json").read_text())
    meta = inscope["meta"]
    system = (ROOT / "system-prompt.md").read_text()

    detected_ist = detected_at.astimezone(IST)
    stamp = stamp_ist(detected_ist)

    transcript = [f"# transcript {tag}  model={MODEL}"]
    metrics = {"iterations": 0, "web_calls": 0}
    bullets = run_agent(context_md, system, transcript, metrics)

    noise_count = sum(1 for c in inscope.get("changes", []) if c.get("noise"))
    summary = build_summary(meta, bullets, noise_count, detected_ist, stamp)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{stamp}.md").write_text(summary)
    (out_dir / f"{stamp}.transcript.log").write_text("\n".join(transcript) + "\n")

    print(f"[{tag}] -> {out_dir / (stamp + '.md')}")
    print(f"  iterations={metrics['iterations']} web_calls={metrics['web_calls']} "
          f"format_matched={meta.get('format_matched', True)}")
    return summary


def main():
    ap = argparse.ArgumentParser(description="Summarise one prefetched rollup via the agent.")
    ap.add_argument("--tag", required=True)
    ap.add_argument("--samples-dir", default=str(ROOT / "samples"))
    ap.add_argument("--out-dir", default=str(ROOT / "out"))
    ap.add_argument("--detected-at", default="",
                    help="ISO8601 detection time (default: now). The summary + filenames use this.")
    args = ap.parse_args()

    _load_env()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set (add it to lab/.env or export it).", file=sys.stderr)
        sys.exit(1)

    if args.detected_at:
        detected = datetime.fromisoformat(args.detected_at)
        if detected.tzinfo is None:
            detected = detected.replace(tzinfo=timezone.utc)
    else:
        detected = datetime.now(timezone.utc)

    run(args.tag, detected, Path(args.samples_dir), Path(args.out_dir))


if __name__ == "__main__":
    main()
