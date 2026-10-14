"""``lumina`` command line.

    lumina serve [--project p.lumina]     run the engine (the GUI starts this)
    lumina mcp                            MCP server on stdio (attaches to a running engine)
    lumina call METHOD [JSON]             one RPC call, e.g. lumina call timeline.state
    lumina exec COMMAND [JSON]            run one command, e.g. lumina exec clip.razor '{"at":"5s"}'
    lumina schema OUT_DIR                 write JSON Schemas for the UI's typed client
"""

from __future__ import annotations

import json
import sys


def _print(obj) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


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
    elif cmd == "schema":
        from .schema_export import write_schemas

        write_schemas(rest[0] if rest else "../shared/schema")
    else:
        raise SystemExit(f"unknown command {cmd!r}\n{__doc__}")
