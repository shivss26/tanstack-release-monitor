#!/usr/bin/env python3
"""Orchestrator — decoupled enrich. Turns detected rollups + yanks into per-item
summaries and ONE combined digest, idempotently and self-healingly.

Decoupled from detect: detect records `raw/<label>/<stamp>__<tag>.json` (and
`yanks/<label>/<stamp>__<tag>.md`) and advances its own watermark. This step does
the PENDING work:
  * a raw rollup with no `summaries/<label>/<stamp>__<tag>.md` yet  -> enrich it,
  * a yank with no `yank-notices/<label>/<stamp>__<tag>.md` yet     -> note it.

`<stamp>` is the DETECTION time (IST, `YYYY-MM-DD-HHMM`), so the raw rollup, the
prefetch context, and the summary all share it; `<tag>` disambiguates multiple
rollups detected in the same run.

A digest (the email body) is written ONLY when this run produced something
REPORTABLE — at least one substantive rollup or one yank. A run that finds nothing
new writes no digest and prints "no email", so empty/dependency-only rollups and
quiet runs never trigger mail.

Edge cases handled: multiple rollups per run (one summary each, shared stamp),
yanks (deterministic notice, no agent), rollup+yank combos (two digest sections),
empty rollups (recorded, not emailed), unrecognised format (emitted WITH the
warning and counted as reportable), crash/retry (pending work is re-derived from
missing summary/notice files).
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import prefetch
import enrich

HERE = Path(__file__).resolve().parent
IST = enrich.IST
NAME_RE = re.compile(r"^(?P<stamp>\d{4}-\d{2}-\d{2}-\d{4})__(?P<tag>.+)$")


def parse_name(path):
    m = NAME_RE.match(path.stem)
    return (m["stamp"], m["tag"]) if m else (None, None)


def stamp_to_ist(stamp):
    return datetime.strptime(stamp, "%Y-%m-%d-%H%M").replace(tzinfo=IST)


def yank_notice(tag, detected_ist):
    return (f"**{tag}** — a previously reported release was deleted or unpublished "
            f"from GitHub (noticed {enrich.fmt_ist(detected_ist)}). Treat its changes "
            f"as retracted; if you adopted anything from it, re-check.")


def process(root, config, token, system):
    summaries, notices = [], []
    for src in config["sources"]:
        label, owner, repo = src["label"], src["owner"], src["repo"]
        libs = set(src.get("libraries") or prefetch.default_libs(repo))

        raw_dir = root / "raw" / label
        for raw_path in sorted(raw_dir.glob("*.json")) if raw_dir.exists() else []:
            stamp, tag = parse_name(raw_path)
            if not stamp:
                print(f"[{label}] skip non-stamped raw file: {raw_path.name}", file=sys.stderr)
                continue
            summ = root / "summaries" / label / f"{stamp}__{tag}.md"
            if summ.exists():
                continue  # already enriched in a prior run (self-healing/idempotent)

            rel = json.loads(raw_path.read_text())
            style = rel.get("style") if isinstance(rel, dict) else None
            if style == "package-batch":
                context, inscope = prefetch.assemble_batch(
                    owner, repo, rel, token, source_libs=libs)
            elif style == "single-package":
                context, inscope = prefetch.assemble_single(
                    owner, repo, rel["release"], token, source_libs=libs)
            else:  # legacy/rollup: the raw record IS the GitHub release JSON
                context, inscope = prefetch.assemble(
                    owner, repo, tag, rel.get("body") or "", rel.get("html_url", ""),
                    token, source_libs=libs)
            meta = inscope["meta"]

            pf = root / "prefetch" / label / f"{stamp}__{tag}.md"
            pf.parent.mkdir(parents=True, exist_ok=True)
            pf.write_text(context)

            changes = inscope["changes"]
            substantive = [c for c in changes if not c["noise"]]
            if meta.get("format_matched", True) and not substantive:
                # Empty / dependency-only rollup: no agent call needed, and nothing
                # to report — write a deterministic note so it isn't re-processed.
                bullets = "_No developer-facing changes (dependency or internal updates only)._"
                reportable = False
            else:
                transcript = [f"# {tag}  model={enrich.MODEL}"]
                metrics = {"iterations": 0, "web_calls": 0}
                bullets = enrich.run_agent(context, system, transcript, metrics)
                reportable = (not meta.get("format_matched", True)) or bool(substantive)
                tdir = root / "transcripts" / label
                tdir.mkdir(parents=True, exist_ok=True)
                (tdir / f"{stamp}__{tag}.log").write_text("\n".join(transcript) + "\n")
                print(f"[{label}] {tag} iterations={metrics['iterations']} "
                      f"web_calls={metrics['web_calls']}")

            noise = sum(1 for c in changes if c["noise"])
            summary = enrich.build_summary(meta, bullets, noise,
                                           stamp_to_ist(stamp), f"{stamp}__{tag}", label)
            summ.parent.mkdir(parents=True, exist_ok=True)
            summ.write_text(summary)
            print(f"[{label}] enriched {tag} (reportable={reportable})")
            if reportable:
                summaries.append((label, tag, summary))

        yank_dir = root / "yanks" / label
        for yank_path in sorted(yank_dir.glob("*.md")) if yank_dir.exists() else []:
            stamp, tag = parse_name(yank_path)
            if not stamp:
                continue
            note_path = root / "yank-notices" / label / f"{stamp}__{tag}.md"
            if note_path.exists():
                continue
            notice = yank_notice(tag, stamp_to_ist(stamp))
            note_path.parent.mkdir(parents=True, exist_ok=True)
            note_path.write_text(notice + "\n")
            print(f"[{label}] yank notice {tag}")
            notices.append((label, tag, notice))

    return summaries, notices


def compose_digest(summaries, notices, run_ist):
    L = [f"# TanStack release digest — {enrich.fmt_ist(run_ist)}", ""]
    if summaries:
        L += [f"## New releases ({len(summaries)})", ""]
        for _, _, text in summaries:
            L += [text.strip(), "", "---", ""]
    if notices:
        L += [f"## Retracted releases ({len(notices)})", ""]
        for _, _, text in notices:
            L.append(f"- {text}")
        L.append("")
    return "\n".join(L).rstrip() + "\n"


def main():
    ap = argparse.ArgumentParser(description="Enrich pending rollups/yanks -> summaries + digest.")
    ap.add_argument("--root", default=str(HERE),
                    help="data root containing raw/, yanks/, summaries/, ... (the monitor repo)")
    ap.add_argument("--config", default="", help="path to config.json (default: <root>/monitor/config.json)")
    args = ap.parse_args()

    enrich._load_env()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set.", file=sys.stderr)
        sys.exit(1)

    root = Path(args.root)
    config_path = Path(args.config) if args.config else root / "monitor" / "config.json"
    config = json.loads(Path(config_path).read_text())
    system = (HERE / "system-prompt.md").read_text()
    token = os.environ.get("GITHUB_TOKEN", "")

    summaries, notices = process(root, config, token, system)

    if summaries or notices:
        run_ist = datetime.now(timezone.utc).astimezone(IST)
        digest = compose_digest(summaries, notices, run_ist)
        dpath = root / "digest" / f"{run_ist.strftime('%Y-%m-%d-%H%M')}.md"
        dpath.parent.mkdir(parents=True, exist_ok=True)
        dpath.write_text(digest)
        print(f"DIGEST ({len(summaries)} new, {len(notices)} retracted) -> {dpath}  [SEND EMAIL]")
    else:
        print("nothing reportable -> NO EMAIL")


if __name__ == "__main__":
    main()
