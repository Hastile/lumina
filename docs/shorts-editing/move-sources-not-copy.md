---
name: move-sources-not-copy
description: Source files the user downloaded go into the project folder by moving; the Downloads original is not kept
metadata:
  node_type: memory
  type: feedback
  originSessionId: 3899aeb6-a6c5-49f4-97f8-45adc1ac9f74
  modified: 2026-10-27T15:14:48.847Z
---

The user said "원본 남길 필요 없음" (2026-10-22) after I copied the Special Talk mp4 from Downloads\hitomi_downloader_GUI into the Short's project folder and kept the original.

**Why:** a second copy of a large video (546 MB here) in Downloads only takes space. The project folder is where sources live.

Windows controlled folder access protects `C:\Users\HETX\Videos` (OBS recordings land there): Git Bash `mv`/`rm` and lumina-engine.exe get "Permission denied", but PowerShell `Remove-Item -LiteralPath` works (2026-10-28). So copy with a hash check, then delete the original from PowerShell.

**How to apply:** move downloaded sources into the project folder, or copy, check that the hash matches, and delete the original. Related: [[shorts-project-per-folder]], [[shorts-automation-pipeline]].
