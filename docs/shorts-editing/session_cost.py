"""What an editing session cost: Lumina calls by tool, frames looked at, tokens and time (no tokens to run).

    python -I docs/shorts-editing/session_cost.py                 # the 10 newest Claude Code sessions of this repo
    python -I docs/shorts-editing/session_cost.py a.jsonl b.jsonl # given transcripts
    python -I docs/shorts-editing/session_cost.py --all --since 2026-10-01

Reads Claude Code transcripts (~/.claude/projects/<project>/<session>.jsonl). Compare a Short made the
old way (540-650 Lumina calls, 100-175 get_frame_image) with one made through shorts_build.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

LOOKS = {"get_frame_image", "get_frame_strip", "source_sheet", "library_contact_sheet", "inspect_timeline_frames"}


def _ts(s: str | None):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None
    except ValueError:
        return None


def session(path: Path) -> dict:
    tools, usage, prompts, first, last = Counter(), {}, 0, None, None
    for line in path.open(encoding="utf-8", errors="replace"):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        t = _ts(r.get("timestamp"))
        if t:
            first, last = first or t, t
        if r.get("type") == "user" and (r.get("origin") or {}).get("kind") == "human":
            prompts += 1
        if r.get("type") != "assistant":
            continue
        m = r.get("message") or {}
        if m.get("id") and m.get("usage"):
            usage[m["id"]] = m["usage"]  # one message is written over several lines; count its usage once
        for c in m.get("content") or []:
            if c.get("type") == "tool_use":
                tools[c.get("name", "?")] += 1
    lumina = Counter({k.split("__", 2)[-1]: v for k, v in tools.items() if k.startswith("mcp__lumina__")})
    u = list(usage.values())
    return {
        "file": path.name, "start": first.isoformat(timespec="minutes") if first else None,
        "minutes": round((last - first).total_seconds() / 60) if first and last else None, "prompts": prompts,
        "lumina_calls": sum(lumina.values()), "looks": sum(v for k, v in lumina.items() if k in LOOKS),
        "other_tools": sum(v for k, v in tools.items() if not k.startswith("mcp__lumina__")),
        "top": lumina.most_common(8),
        "tokens": {"input": sum(x.get("input_tokens", 0) + x.get("cache_creation_input_tokens", 0) for x in u),
                   "cache_read": sum(x.get("cache_read_input_tokens", 0) for x in u),
                   "output": sum(x.get("output_tokens", 0) for x in u)},
        "requests": len(u),
    }


def default_files(all_: bool, n: int) -> list[Path]:
    root = Path.home() / ".claude" / "projects"
    dirs = [d for d in root.glob("*") if d.is_dir() and "lumina" in d.name.lower()]
    files = sorted((f for d in dirs for f in d.glob("*.jsonl")), key=lambda f: f.stat().st_mtime, reverse=True)
    return files if all_ else files[:n]


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("-n", type=int, default=10)
    ap.add_argument("--since", help="YYYY-MM-DD")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    files = [Path(f) for f in a.files] or default_files(a.all, a.n)
    rows = [session(f) for f in files]
    if a.since:
        rows = [r for r in rows if r["start"] and r["start"] >= a.since]
    rows = [r for r in rows if r["lumina_calls"] or a.files]
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return
    if not rows:
        print("no sessions with Lumina calls found", file=sys.stderr)
        return
    for r in sorted(rows, key=lambda r: r["start"] or ""):
        tk = r["tokens"]
        print(f"{r['start']}  {r['minutes']:>4} min  {r['prompts']:>3} prompts  lumina {r['lumina_calls']:>4} "
              f"(looks {r['looks']:>3})  other {r['other_tools']:>4}  tokens in {tk['input']:,} cached {tk['cache_read']:,} "
              f"out {tk['output']:,}  {r['file']}")
        print("     " + ", ".join(f"{k} {v}" for k, v in r["top"]))


if __name__ == "__main__":
    main()
