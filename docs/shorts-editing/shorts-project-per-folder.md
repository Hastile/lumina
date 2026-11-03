---
name: shorts-project-per-folder
description: "A new Short is a sequence inside the folder's one Lumina project, never a project of its own"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 3899aeb6-a6c5-49f4-97f8-45adc1ac9f74
  modified: 2026-10-27T14:22:39.945Z
---

The user said "프로젝트는 쪼개지 마셈" (2026-10-22) when I made a .lumina per Short. They then chose "엣지러너 2 안에 통합 프로젝트".

The setup that matches:
- One project per work folder, named after the folder: `…\사이버펑크\엣지러너 2\엣지러너 2.lumina`, from templates/caddynote.lumina.
- Each Short is a copy of the template sequence `시퀀스 02 (쇼츠 제목 폴더명 (복사해서 사용))`, renamed `시퀀스 02 (<short title>)`, as in 팜 술.lumina's `시퀀스 02 (팜술 출시일 떴다)`.
- The base `시퀀스 02 (shorts)` stays empty.
- Narration (`<short title>.wav`) and captions (`<short title>.srt`) live next to the project and keep the Short's title.
- If the folder already has its project, add the new sequence to it instead of creating one.
- A per-Short folder the user asks for ("탈리아 양 폴더 만들고", 2026-10-23) is a subfolder of the work folder for that Short's files (`…\엣지러너 2\탈리아 양\` holds the wav, srt and raw TTS). The sequence still goes in the work's project (엣지러너 2.lumina). I first made `…\사이버펑크\탈리아 양\탈리아 양.lumina` and the user corrected: "엣지러너 2 안에 넣어야지".

- When the top folder is a franchise holding one folder per character (`Project\나루토\나루토`, `…\오로치마루`), the character folder is the project folder: `…\나루토\우치하 사스케\우치하 사스케.lumina`. Folder and project use the character's full name (우치하 사스케, not 사스케). I first made `나루토\사스케\` with `나루토\나루토.lumina` and the user corrected (2026-10-27): "우치하 사스케 하고 안에 사스케 lumina로 해야지". Obsidian mirrors it (`Caddynote\나루토\우치하 사스케\`).

- Setup before footage, as in 엣지러너 2's 탈리아/오미 sequences: a bin `shorts/<short title>` holds `<title>.wav`, the raw take imported and renamed `<title> - 원본.mp3`, and the sequence. The A1 wav is tagged dialogue. V4 is named `TMI`. The A1 clip ends where the voice ends, and V5, V6 and the last caption end 6 frames later.

**Why:** the user's Premiere projects work the same way. 루시.prproj holds several Shorts sequences.

**How to apply:** at step 3 (Lumina setup), open or create the folder's project first, then duplicate and rename the copy sequence. Related: [[shorts-automation-pipeline]], [[lumina-mcp-visible-work]].
