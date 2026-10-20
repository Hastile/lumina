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

## Build (Windows installer)

```sh
cd app && npm run dist        # -> app/release/Lumina-Setup-<version>.exe
npm run dist:dir              # unpacked app only: app/release/win-unpacked/Lumina.exe
```

`scripts/build-exe.mjs` freezes the engine with PyInstaller (`lumina-engine.exe`),
copies `ffmpeg`/`ffprobe` and their DLLs from PATH into the app's resources, then
runs electron-builder. The installed app needs neither `uv`, Python nor FFmpeg.

## Automate

While the GUI is open, its engine writes `~/.lumina/engine.json`. Agents and
scripts attach to that engine, so their edits show up live in the open window.
With no GUI running, they start a headless engine instead.

- MCP: `.mcp.json` in this repo registers `lumina mcp`. It exposes one tool per
  command (`clip_place`, `clip_razor`, `sequence_remove_ranges`, …) plus
  `get_timeline_state`, `undo`, `begin_transaction` / `commit_transaction` /
  `rollback_transaction`, `save_project`, `export_media` (render + ffprobe
  verification) and `get_frame_image` (returns the rendered frame as an image so
  an agent can look at its edit).
- Shorts copy: `shorts_set_copy` sets the title copy on the graphic clip named
  `쇼츠 타이틀` (sub = upper text layer, main = lower). Each line is rendered in
  its own style and measured where it lands on screen; a line that would run
  past the frame (or past `margin`), contains a line break, or uses a character
  the font cannot draw is refused with its bounds and `max_hangul`. `dry_run`
  only measures. With the channel template (sub S-CoreDream-5Medium 100, main
  S-CoreDream-9Black 115, tracking -18, 1080 wide) that is 11 Hangul syllables
  for the sub line and 9 for the main line; a space is about 0.3 of a syllable.
  S-CoreDream only has the 2,350 KS X 1001 syllables, so e.g. 똠 and 뷁 render blank.
  `hide` + `reveal_at` is the channel's reveal: the title opens with those words
  as `?` (one per character, particles stay visible: `?????을 파쿠리한`) and is
  cut at `reveal_at`, the start of the caption where the narration first says them.
- CLI: `uv run lumina exec clip.razor '{"at":"5s"}'` and `uv run lumina call timeline.state`.
- Headless export: `uv run lumina render project.lumina out.mp4 [--sequence NAME] [--preset h264|hevc|prores|wav] [--range in_out]`.
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
