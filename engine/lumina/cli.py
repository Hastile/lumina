"""``lumina`` command line.

    lumina serve [--project p.lumina]     run the engine (the GUI starts this)
    lumina mcp                            MCP server on stdio (attaches to a running engine)
    lumina call METHOD [JSON]             one RPC call, e.g. lumina call timeline.state
    lumina exec COMMAND [JSON]            run one command, e.g. lumina exec clip.razor '{"at":"5s"}'
    lumina render PROJECT OUT [--sequence NAME|ID] [--preset h264|hevc|prores|wav] [--range entire|in_out]
                                          headless export of a saved project (no GUI needed)
    lumina schema OUT_DIR                 write JSON Schemas for the UI's typed client
"""

from __future__ import annotations

import json
import sys


def _print(obj) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def _render(argv: list[str]) -> None:
    import argparse

    from .commands.registry import CommandError
    from .engine import Engine

    ap = argparse.ArgumentParser(prog="lumina render")
    ap.add_argument("project")
    ap.add_argument("out")
    ap.add_argument("--sequence", help="name or id; default the active sequence")
    ap.add_argument("--preset", default="h264")
    ap.add_argument("--range", default="entire", choices=["entire", "in_out"])
    ap.add_argument("--hardware", default="auto", choices=["auto", "on", "off"])
    args = ap.parse_args(argv)
    engine = Engine(run_jobs=False)
    engine.dispatch("project.open", {"path": args.project})
    seq_id = None
    if args.sequence:
        seqs = engine.session.doc["sequences"]
        seq_id = args.sequence if args.sequence in seqs else next((k for k, v in seqs.items() if v["name"] == args.sequence), None)
        if seq_id is None:
            raise SystemExit(f"no sequence {args.sequence!r}; have: {[v['name'] for v in seqs.values()]}")

    def progress(ev: dict) -> None:
        job = ev.get("job") if ev.get("type") == "job" else None
        if job and job.get("kind") == "export" and job.get("status") == "running":
            print(f"\r{job.get('phase', '')} {job.get('progress', 0) * 100:5.1f}%  {job.get('fps') or '':>6} fps", end="", file=sys.stderr, flush=True)

    engine.subscribe(progress)
    try:
        job = engine.dispatch("export.start", {"sequence_id": seq_id, "path": args.out, "preset": args.preset,
                                               "range": args.range, "hardware": args.hardware, "wait": True})
    except CommandError as exc:
        _print({"error": exc.to_dict()})
        raise SystemExit(1)
    print(file=sys.stderr)
    _print(job["result"] if job["status"] == "done" else {"status": job["status"], "error": job["error"]})
    if job["status"] != "done":
        raise SystemExit(1)


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return
    cmd, rest = argv[0], argv[1:]
    if cmd == "serve":
        from .server import main as serve

        serve(rest)
    elif cmd == "mcp":
        from .mcp_server import main as mcp_main

        mcp_main()
    elif cmd in ("call", "exec"):
        from .client import connect
        from .commands.registry import CommandError

        if not rest:
            raise SystemExit(f"usage: lumina {cmd} NAME [JSON]")
        params = json.loads(rest[1]) if len(rest) > 1 else {}
        engine = connect()
        try:
            if cmd == "exec":
                _print(engine.dispatch("execute", {"name": rest[0], "params": params}))
            else:
                _print(engine.dispatch(rest[0], params))
        except CommandError as exc:
            _print({"error": exc.to_dict()})
            raise SystemExit(1)
        finally:
            engine.close()
    elif cmd == "render":
        _render(rest)
    elif cmd == "schema":
        from .schema_export import write_schemas

        write_schemas(rest[0] if rest else "../shared/schema")
    else:
        raise SystemExit(f"unknown command {cmd!r}\n{__doc__}")
