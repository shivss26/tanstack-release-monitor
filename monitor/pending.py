#!/usr/bin/env python3
"""Count PENDING enrich work — stdlib only, so the workflow can cheaply gate the
heavy enrich setup (Node + TanStack CLI + uv sync) on whether there's anything to do.

Pending = a detected rollup or yank that the orchestrator hasn't turned into an
output yet (same decoupled/self-healing rule enrich_run.py uses):
  * raw/<label>/<stamp>__<tag>.json   with no summaries/<label>/<stamp>__<tag>.md
  * yanks/<label>/<stamp>__<tag>.md   with no yank-notices/<label>/<stamp>__<tag>.md

Prints the integer total to stdout. The workflow runs enrich only when it's > 0.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # monitor/ -> repo root
CONFIG_PATH = ROOT / "monitor" / "config.json"
NAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{4}__")  # <stamp>__<tag>


def _pending(src_dir, out_dir, suffix):
    """Items in src_dir whose matching output file in out_dir is missing."""
    if not src_dir.exists():
        return 0
    n = 0
    for path in src_dir.glob("*"):
        if not path.is_file() or not NAME_RE.match(path.stem):
            continue
        if not (out_dir / f"{path.stem}{suffix}").exists():
            n += 1
    return n


def main():
    config = json.loads(CONFIG_PATH.read_text())
    total = 0
    for src in config["sources"]:
        label = src["label"]
        total += _pending(ROOT / "raw" / label, ROOT / "summaries" / label, ".md")
        total += _pending(ROOT / "yanks" / label, ROOT / "yank-notices" / label, ".md")
    print(total)


if __name__ == "__main__":
    main()
