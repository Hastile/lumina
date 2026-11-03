---
name: obsidian-script-records
description: Every finalized Caddynote script (Shorts and long-form) must be saved/updated in D:\Obsidian\Caddynote as part of the automation; recent ones were often missing
metadata:
  node_type: memory
  type: feedback
  originSessionId: 1646aaed-0f38-4ee0-ba93-a882c0988aea
  modified: 2026-10-23T19:21:55.132Z
---

When automating Shorts/long-form work, save the latest script to Obsidian (`D:\Obsidian\Caddynote\<작품>\<하위폴더>\(shorts) <이름>.md`, long-form without "(shorts)"), and re-save when the narration changes. Tool: `~/.claude/skills/caddynote-script/obsidian.py save …` (matches existing folders, moves the old version to the vault `.trash`). Format = the user's own notes: no front matter, one sentence per line, TMI in parentheses, no upload title/tags.

**Why:** the user said (2026-10-24) "자동화할때 obsidian에 대본 최신화. 최근에 기록을 안하는 경우가 많았음" — the vault is their script archive; the last Shorts record was 2026-08-28 while uploads continued (37 SRTs since 2026-08-25 had no Obsidian script).
**How to apply:** save at the moment the script is fixed (user confirms, TTS generated, or Lumina editing starts) — step 1.4 in docs/shorts-editing/README.md and §6 of the caddynote-script skill. Missing ones can be restored from project SRTs with `obsidian.py backfill --since … --apply` (narration only, no TMI; marked with a %% comment). Related: [[caddynote-script-guidelines]], [[shorts-automation-pipeline]].
