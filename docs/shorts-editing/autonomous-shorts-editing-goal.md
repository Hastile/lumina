---
name: autonomous-shorts-editing-goal
description: "User wants Claude to decide cut and SFX placement for Shorts on its own, following the channel's editing grammar"
metadata:
  node_type: memory
  type: project
  originSessionId: f11937d7-bae0-47c4-9b34-1f94cbcc00d8
  modified: 2026-10-17T16:49:54.141Z
---

Goal (stated 2026-10-18): for future Shorts in Lumina, Claude should decide cut allocation and SFX allocation itself; the channel has a basic editing grammar.
Reference edit: 팜 술.lumina, sequence "시퀀스 02 (팜술 출시일 떴다)"; MCP-rebuilt copy "시퀀스 02 (팜술 출시일 떴다 (자동화 테스트))" for automation tests. V7 captions there were left undone (a past change issue).
Observed grammar: cuts on caption-cue boundaries; A2 whoosh on big transitions/opening, A3 kick/hit on emphasis, A4 bells on reveals, "티딩!" at the end; SFX tagged sfx and auto-matched to -21 LUFS; video at 130% fill (y 1060), stills get slide/zoom moves.

**Why:** the user is building toward automated Shorts editing through Lumina MCP.
**How to apply:** do edits through Lumina MCP so the user can watch them happen (they asked to see the work, not hidden scripts); validate against the reference edit before trusting new rules. See [[lumina-mcp-visible-work]].
