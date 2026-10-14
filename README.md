# Lumina

A Premiere Pro–style video editor built for full automation. Every edit,
whether from a GUI click, a Premiere shortcut or an MCP tool call, is a single
undoable engine command.

```
app/      Electron + React GUI: Premiere "Editing" workspace, keymap, monitors, timeline
engine/   Python engine: document model, reversible commands, RPC + MCP servers, media jobs
shared/   JSON Schemas generated from the engine (the UI's TypeScript types are generated from them)
```

## Run

Requirements: `uv`, Node 22+ and FFmpeg (`ffmpeg`/`ffprobe` on PATH).

```sh
cd engine && uv sync
cd ../app && npm install && npm run dev    # starts the GUI, which spawns the engine
```

## Automate

While the GUI is open, its engine writes `~/.lumina/engine.json`. Agents and
scripts attach to that engine, so their edits show up live in the open window.
With no GUI running, they start a headless engine instead.

- MCP: `.mcp.json` in this repo registers `lumina mcp`. It exposes one tool per
  command (`clip_place`, `clip_razor`, `sequence_remove_ranges`, …) plus
  `get_timeline_state`, `undo`, `begin_transaction` / `commit_transaction` /
  `rollback_transaction`, and `save_project`.
- CLI: `uv run lumina exec clip.razor '{"at":"5s"}'` and `uv run lumina call timeline.state`.
- Time values accept ticks, `"12.5s"`, `"45f"` or `"00:00:12:15"`.

## Test

```sh
cd engine && uv run pytest        # model/command property tests + ported adb-mcp suite
cd app && npm run typecheck && npm run e2e   # drives the real app with Premiere shortcuts
```

`app/test/e2e.mjs` needs `samples/test30.mp4`. Generate it with:

```sh
ffmpeg -f lavfi -i "testsrc2=size=1280x720:rate=30:duration=12" -f lavfi -i "sine=frequency=440:duration=12" \
  -vf "drawtext=text='%{frame_num}':x=40:y=40:fontsize=72:fontcolor=white:box=1:boxcolor=black" \
  -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest samples/test30.mp4
```
