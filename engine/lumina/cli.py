"""``lumina`` command line.

    lumina serve [--project p.lumina]     run the engine (the GUI starts this)
    lumina mcp                            MCP server on stdio (attaches to a running engine)
    lumina call METHOD [JSON]             one RPC call, e.g. lumina call timeline.state
    lumina exec COMMAND [JSON]            run one command, e.g. lumina exec clip.razor '{"at":"5s"}'
    lumina render PROJECT OUT [--sequence NAME|ID] [--preset h264|hevc|prores|wav] [--range entire|in_out]
                                          headless export of a saved project (no GUI needed)
    lumina index FOLDER [--transcribe auto|on|off] [--language ko] [--force]
                                          index source footage: shots, keyframes, quality flags, transcript
    lumina search FOLDER QUERY            find shots by description, tags, transcript or file name
    lumina build PLAN.json [--out OUT.mp4] [--project P.lumina]
                                          assemble a whole edit from a JSON edit plan (see lumina.autoedit)
    lumina auto FOLDER "BRIEF" [--duration 60] [--aspect 9:16] [--out OUT.mp4] [--model ID] [--no-describe]
                                          index, describe shots, plan with Claude, build, save and export
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


def _say(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _index(argv: list[str]) -> None:
    import argparse

    from . import library

    ap = argparse.ArgumentParser(prog="lumina index")
    ap.add_argument("folder")
    ap.add_argument("--transcribe", default="auto", choices=["auto", "on", "off"])
    ap.add_argument("--whisper-model", default="small")
    ap.add_argument("--language")
    ap.add_argument("--scene-threshold", type=float, default=0.3)
    ap.add_argument("--min-shot", type=float, default=1.0)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    _print(library.index(a.folder, force=a.force, transcribe_mode=a.transcribe, whisper_model=a.whisper_model, language=a.language,
                         scene_threshold=a.scene_threshold, min_shot=a.min_shot, progress=_say))


def _search(argv: list[str]) -> None:
    import argparse

    from . import library

    ap = argparse.ArgumentParser(prog="lumina search")
    ap.add_argument("folder")
    ap.add_argument("query")
    ap.add_argument("--kind", choices=["video", "audio", "image"])
    ap.add_argument("--limit", type=int, default=20)
    a = ap.parse_args(argv)
    _print(library.search(a.folder, a.query, kind=a.kind, limit=a.limit))


def _build_and_report(plan: dict) -> None:
    from .client import LocalEngine

    engine = LocalEngine(run_jobs=False)
    try:
        engine.dispatch("project.new", {"name": plan.get("title") or "Auto Edit"})
        if plan.get("output"):
            _say(f"rendering {plan['output']} ...")
        _print(engine.dispatch("edit.build", {"plan": plan}))
    finally:
        engine.close()


def _build(argv: list[str]) -> None:
    import argparse
    from pathlib import Path

    ap = argparse.ArgumentParser(prog="lumina build")
    ap.add_argument("plan")
    ap.add_argument("--out", help="export here (overrides plan.output)")
    ap.add_argument("--project", help="save the project here (overrides plan.project)")
    a = ap.parse_args(argv)
    plan = json.loads(Path(a.plan).read_text(encoding="utf-8"))
    if a.out:
        plan["output"] = a.out
    if a.project:
        plan["project"] = a.project
    _build_and_report(plan)


def _auto(argv: list[str]) -> None:
    import argparse
    from pathlib import Path

    from . import autoedit, library

    ap = argparse.ArgumentParser(prog="lumina auto")
    ap.add_argument("folder")
    ap.add_argument("brief")
    ap.add_argument("--duration", type=float, help="target length in seconds")
    ap.add_argument("--aspect", choices=sorted(autoedit.ASPECTS))
    ap.add_argument("--out", help="output video (default <folder>/.lumina/out/<title>.mp4)")
    ap.add_argument("--project", help="project file (default next to the output)")
    ap.add_argument("--model", default=autoedit.DEFAULT_MODEL)
    ap.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
    ap.add_argument("--transcribe", default="auto", choices=["auto", "on", "off"])
    ap.add_argument("--language")
    ap.add_argument("--no-describe", action="store_true", help="skip the vision pass that describes shots")
    ap.add_argument("--plan-only", action="store_true", help="write the plan JSON and stop")
    a = ap.parse_args(argv)

    _say("indexing ...")
    library.index(a.folder, transcribe_mode=a.transcribe, language=a.language, progress=_say)
    if not a.no_describe:
        autoedit.describe_shots(a.folder, model=a.model, progress=_say)
    _say("planning ...")
    plan = autoedit.plan_with_claude(a.folder, a.brief, duration=a.duration, aspect=a.aspect, model=a.model, effort=a.effort)
    out_dir = library.index_dir(a.folder) / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = "".join(c if c.isalnum() or c in "-_ " else "_" for c in plan["title"]).strip() or "auto-edit"
    plan_path = out_dir / f"{stem}.plan.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    _say(f"plan: {plan_path}  ({len(plan['clips'])} clips) {plan.get('notes', '')}")
    if a.plan_only:
        return
    out = Path(a.out) if a.out else out_dir / f"{stem}.mp4"
    plan["output"] = str(out)
    plan["project"] = a.project or str(out.with_suffix(".lumina"))
    _build_and_report(plan)


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
    elif cmd in ("index", "search", "build", "auto"):
        from .commands.registry import CommandError

        try:
            {"index": _index, "search": _search, "build": _build, "auto": _auto}[cmd](rest)
        except CommandError as exc:
            _print({"error": exc.to_dict()})
            raise SystemExit(1)
    elif cmd == "schema":
        from .schema_export import write_schemas

        write_schemas(rest[0] if rest else "../shared/schema")
    else:
        raise SystemExit(f"unknown command {cmd!r}\n{__doc__}")
