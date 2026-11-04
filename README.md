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
The default MCP starts idle without a headless engine. `get_editor_status` checks
the GUI without launching it; `ensure_editor(project_path)` starts/connects the
GUI and prepares the requested project. An already open target is activated
without reloading unsaved edits. Other open projects stay open when a new target
is opened. A later manual project switch blocks edits until the target is
confirmed again. GUI launch failures never fall back to headless editing.
`LUMINA_HEADLESS=1` is an explicit opt-in for requested unattended work; CLI
`call`/`exec` and `render` retain their separate behavior. See the
[MCP lifecycle and three project cases](docs/mcp-lifecycle.md).

[Source review tab](docs/source-review.md): active-source ingest, frame-boundary inspection,
IP/character/dialogue correction and reviewed publishing to Lumina's source database.

Development, unpacked and installed GUI builds share the `Lumina` profile and
single-instance lock. A second launch focuses the existing editor and cannot
start an invisible engine. `LUMINA_HOME` isolates both discovery and GUI profile
for tests/custom instances; all clients must use the same value. The renderer
recovers missing events with a snapshot heartbeat without reopening the file.
After updating client code, reconnect an already-running MCP session once so it
loads the new code.

Integration check (isolated temporary home, persistent real MCP stdio session):
`cd app; npm run build; node test/mcp-gui-sync.mjs`. To verify packaged binaries,
set `LUMINA_TEST_EXE` to `release/win-unpacked/Lumina.exe` and
`LUMINA_TEST_MCP_EXE` to its `resources/engine/lumina-engine.exe`.
`LUMINA_TEST_SECOND_EXE` can point to another GUI build to verify the shared lock.

- MCP: `.mcp.json` in this repo registers `python -m lumina mcp` (equivalent to
  `lumina mcp`, without a Windows launcher named like the GUI installer target).
  It exposes one tool per
  command (`clip_place`, `clip_razor`, `sequence_remove_ranges`, …) plus
  `get_timeline_state`, `undo`, `begin_transaction` / `commit_transaction` /
  `rollback_transaction`, `save_project`, `export_media` (render + ffprobe
  verification) and `get_frame_image` (returns the rendered frame as an image so
  an agent can look at its edit).
- Templates: `new_project` with `template` (GUI: File > New Project from Template...) starts an unsaved copy of
  a `.lumina` project; the template file is only read. `templates/caddynote.lumina` is the channel template
  (from its Premiere template): both Shorts sequences carry three caption tracks, each with one effect preset
  as a track effect (커졌다 줄어들기, 꽝 박히기, 밑에서 스무쓰하게 올라오기), and A2-A4 at -3 dB, A5 at -6 dB.
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
