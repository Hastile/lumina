# Lumina

Premiere-style video editor (Electron app in `app/`, Python engine in `engine/`). Projects are edited through the Lumina MCP server (`uv run --project engine lumina mcp`).

## Shared Caddynote editing grammar (Codex / Claude)

Before planning or editing Caddynote Shorts or long-form videos, read `docs/editing-grammar/README.md` and the relevant linked grammar/workflow files. Common cut grammar applies to both formats. Learn the purpose of reference edits and improve their clarity; do not reproduce defects or treat clip-duration statistics as hard cut rules. For Hoshino, the reference is `인계 파일/타카나시 호시노.prproj`, sequence `시퀀스 09`.

## Shorts-specific instructions

Before editing a Short in a Lumina project, read `docs/shorts-editing/CARD.md` (one page: the tool flow and the judgments the tools cannot make). Open `docs/shorts-editing/README.md` and the grammar files only for what the card does not cover or the user newly corrects. The user's requests are terse (a Short's name, a pasted file path, "마무리"); the table at the top of the card says what each one means. Carry out the whole flow without asking for the steps. Edit through the Lumina MCP tools so the user can watch the work in the app; do not change `.lumina` files with side scripts.

## MCP tool profile

The Lumina MCP server lists every tool (179, ~149k characters of schemas) unless `LUMINA_TOOLS` narrows it: `shorts` (82), `longform` (134), `core` (54), `all` (default), or a comma list of profiles, tool names and `prefix*` patterns (e.g. `shorts,clip_slip`). Set it in the MCP server's environment for clients that send every tool schema with each request. A hidden tool still answers if called by name.
