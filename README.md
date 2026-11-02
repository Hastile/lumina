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
- CLI: `uv run lumina exec clip.razor '{"at":"5s"}'` and `uv run lumina call timeline.state`.
- Headless export: `uv run lumina render project.lumina out.mp4 [--sequence NAME] [--preset h264|hevc|prores|wav] [--range in_out]`.
- Time values accept ticks, `"12.5s"`, `"45f"` or `"00:00:12:15"`.

## Automatic edits from a folder of footage

Agents spend their limits on round trips (a tool call per clip, frames to look at in between). Lumina moves
the per-clip work into the engine: the footage is logged once as text, and a whole edit arrives as one plan.

```sh
cd engine
uv sync --extra auto --extra transcribe        # Claude planner + local speech-to-text (both optional)
uv run lumina index ~/footage/trip --language ko   # shots, keyframes, dark/soft flags, sound, transcript
uv run lumina search ~/footage/trip "해변"
uv run lumina auto ~/footage/trip "부산 여행 브이로그, 밝고 경쾌하게" --duration 60 --aspect 9:16
uv run lumina build plan.json --out cut.mp4      # any JSON plan, written by you, a script or an agent
```

- `lumina index` stores `<folder>/.lumina/index.json` and re-analyses only changed files. Transcripts need
  `faster-whisper` (runs locally; the model downloads on first use).
- `lumina auto` describes undescribed shots from contact sheets (one Claude vision request per file, cached in
  the index; `--no-describe` skips it), makes one planning request, then builds, saves and exports. It needs an
  Anthropic API key (`ANTHROPIC_API_KEY` or `ant auth login`). `--plan-only` writes the plan and stops.
- Plan format: see the `lumina/autoedit.py` docstring. Clips reference shot ids or files with in/out seconds,
  plus transition, caption and mute; the plan also takes format/aspect, music and transcript subtitles.
- MCP agents (Claude Code, Codex) get the same as tools: `library_index`, `library_overview`,
  `library_search`, `library_contact_sheet`, `library_annotate` and `build_edit`. A full edit is then a handful
  of calls instead of hundreds.

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
