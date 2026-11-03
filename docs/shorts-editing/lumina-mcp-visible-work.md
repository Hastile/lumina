---
name: lumina-mcp-visible-work
description: "When editing a user's Lumina project, act through Lumina MCP tools visibly, not hidden engine scripts"
metadata:
  node_type: memory
  type: feedback
  originSessionId: f11937d7-bae0-47c4-9b34-1f94cbcc00d8
  modified: 2026-10-17T16:50:05.920Z
---

Do project edits (and reads) through Lumina MCP tools with the target sequence open, narrating each step; don't fetch or change the document with side-channel scripts.

**Why:** the user rejected a hidden script that read the engine document and said "내가 볼 수 있게 해줘"; for missing read capability they accepted adding an MCP tool (get_clips) instead.
**How to apply:** if MCP lacks a capability, propose adding the tool rather than working around it. Related: [[autonomous-shorts-editing-goal]].
