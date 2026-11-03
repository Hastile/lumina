---
name: lumina-replace-keep-neighbors
description: "When replacing a clip or sound through Lumina MCP, check what the overwrite covered and restore neighbours it buried"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 8b3fa121-2318-4ff0-bc75-804fc8bd03ba
  modified: 2026-10-21T15:57:18.200Z
---

User, 2026-10-22: when replacing a source through MCP, if valid clips before or after it got buried, adjust them back.

It happened in the 퓨키 Short: an Epic Hit 3 placed with its full length overwrote the Kick 2 on the next caption, and the user had to notice it.

**Why:** an overwrite placement silently trims or deletes the neighbours on that track. Sound tails and the +1-frame safety overshoot are the usual culprits.

**How to apply:**
- Read `covered` in the result of clip.place and clip.move (added in commit 78af49f). It lists each covered clip with its range before (`was`) and the parts left (`now`).
- When a covered clip was a deliberate neighbour (a sound on the next caption, the next shot), do one of these:
  - trim the new clip so it ends at the neighbour's start, or
  - restore the neighbour.
- A sound tail running into the next sound on the same track should be cut, not the next sound.

Related: [[lumina-mcp-visible-work]], [[shorts-sfx-grammar]].
