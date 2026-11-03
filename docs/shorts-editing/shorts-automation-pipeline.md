---
name: shorts-automation-pipeline
description: "User's 6-step Shorts automation plan (TTS → limiter → Phrase Studio SRT → Lumina setup → footage DB → edit → export) and what is done"
metadata:
  node_type: memory
  type: project
  originSessionId: 8b3fa121-2318-4ff0-bc75-804fc8bd03ba
  modified: 2026-10-22T12:40:18.617Z
---

Plan stated 2026-10-20: 1) TTS (ElevenLabs, see [[elevenlabs-tts]]) then limiter, 2) SRT in Phrase Studio (D:\Dev\phrase-studio) cut for Shorts, 3) Lumina project setup, 4) footage search/OBS recording with near-zero Claude/Codex tokens, 5) footage DB (dedupe + AI understanding), 6) edit with sources/SFX/caption effects, 7) export.

- Audition step replaced: user's Hard Limiter (ceiling -4.0 dB, boost 0, look-ahead 7 ms, release 100 ms) = ffmpeg `volume=-0.46dB,alimiter=limit=0.630957:attack=7:release=200:level=false:latency=true` (fitted 2026-10-22 against the user's Audition render of the 퓨키 take: Audition also lowers everything by a constant 0.46 dB, and its 100 ms release matches ffmpeg release=200; per-10 ms gain differs by median 0.06 dB, residual -30 dB, about the MP3 floor), applied straight to the ElevenLabs take: the user's only post-processing in Audition is this Hard Limiter (선택적 제한), no EQ/loudnorm, so D:\Dev\tts post.py is not part of the chain (corrected 2026-10-22). Not yet automated.
- Phrase Studio (git-initialized 2026-10-20): Whisper large-v3 fixed on CUDA, Shorts splitter `shorts_split.py` learned from hand-split projects (CV F1 0.86); retrain via tests/shorts/train.py as more hand edits exist. User pre-splits scripts by line breaks: short lines (≤4 words/14 chars) are kept as captions; breaks after long lines are treated as paste wraps (user confirmed wanting pre-split honored, 2026-10-20). Text edits keep word times (editing.js retext), no forced retime.
- Shorts title copy (2026-10-20): graphic clip "쇼츠 타이틀" on V6, sub = upper layer (S-CoreDream-5Medium 100), main = lower (S-CoreDream-9Black 115, #3a6fb3). Limits at the 1080 frame edge: sub 11, main 9 Hangul (verified in Program monitor). Set via Lumina `shorts.set_copy` (fit-checked, dry_run, default margin 0 — real copies sit up to the edge). Grammar: [[shorts-copy-grammar]]; SFX: [[shorts-sfx-grammar]]. S-CoreDream lacks non-KS X 1001 syllables (똠, 뷁).
- Lumina template (2026-10-21): D:\Dev\lumina\templates\caddynote.lumina (the user wants it in the Lumina dev folder), imported from C:\Users\HETX\OneDrive\문서\Adobe\Common\Assets\Project Templates\caddynote.prproj.
  - Both Shorts sequences ("시퀀스 02 (shorts)" and "…(쇼츠 제목 폴더명 (복사해서 사용))") have three caption tracks above V6, each with a preset as a track effect: 커졌다 줄어들기, 꽝 박히기 and 밑에서 스무쓰하게 올라오기. All three are 100 px (the user wants 100 as the default; 120 is a per-cue override).
  - Caption style is Pretendard-Bold, white, stroke #0c0c0c 3, tracking -30, box 810×576 at (135, 767), with a shadow.
  - Track faders: A2–A4 -3 dB, A5 -6 dB.
  - Start a project with new_project(template=...) (commit 12d10cf); it is unsaved, so the template is never overwritten.
- Footage DB done on 2026-10-22 (commit c99ca45):
  - Code is `engine/lumina/sourcedb`; the DB is `~/.lumina/source.db`, created empty except the lexicon.
  - GPU analyzers run in their own env, `engine/analyzers`: `uv run`, torch cu130, plus CUDA 12 cuBLAS/cuDNN wheels for Whisper. Run the speech stage in its own process.
  - Models are in `~/.lumina/models`.
  - Pilot on the PUKEY folder: 236 cuts. Claude description cost about $0.004 per cut. Searching with the 퓨키 edit's own captions, without its usage rows, put the user's chosen shot in the top 8 for 15–16 of 22 captions.
  - Real DB loaded on 2026-10-22 (commit f39a706) from three folders: PUKEY, 봇치 더 록 and 장송의 프리렌. 43 media, 357 cuts.
    - Works: the right work was accepted for 40 of 43 media.
    - Characters: the folder's main character was accepted in 12 of 12 clips.
    - Search: 18 of 22 captions found their shot in the top 8.
    - Cost: Claude descriptions $1.65 for 41 sheets.
  - Whole Project folder loaded on 2026-10-22 (commits ebc192f, d1216b0):
    - 4,514 media, 50,023 cuts, 5,151 usage rows from all 232 projects.
    - Tagger, Whisper and SigLIP done for everything.
    - Claude descriptions only for the first 43 media. All 50k cuts would be about $230 notional, so the user picks the scope.
    - Old projects point at C:\Users\tmdal\OneDrive\유튜브 채널\...; `ingest.locate` maps them to D:\유튜브 채널.
    - CLI: `python -m lumina.sourcedb ingest|usage-all|describe|verify|reprobe|status`.
    - The analyzer run was once killed for low memory. Run the analyzer stages one at a time.
  - Recording step built 2026-10-22: `python -m lumina.sourcedb.recorder`.
    - How the user records: Chrome plays the episode and an OBS window capture records it; obs-websocket is on port 4455.
    - OBS writes timestamp-named files to C:\Users\HETX\Videos. The user used to rename them by hand to "S2 E04 0931 대사".
    - The recorder reads the work, episode and player time from the extension in `sourcedb/recorder_ext`, which is loaded unpacked in Chrome.
    - It moves each recording into the folder set with `recorder folder <path>`, names it "S2 E16 0931 <first Whisper line>" (the user chose this), and ingests it.
    - Not yet tested against live OBS.
    - Automatic recording, chosen by the user over only auto-starting the helper: `recorder plan clips.txt`.
      - Line format: "S2 E16 09:20-09:45 [url] [note]"; "folder:" lines switch the project folder.
      - The extension (v1.1) seeks and plays on the recorder's command, and the recorder drives OBS StartRecord/StopRecord.
      - Claude can write the plan file from an edit plan.
  - Episodes are parsed from file names: S#E#, E#/EP#, N기 N화, 第N話, Linkkf "😜 N - Anime", OP/ED, Part, game version.
  - Names resolve through the shared entity catalog (D:\Dev\shared\entity_catalog.db).
  - The channel's own names are seeded by hand: work 퓨키 가디스 샷 트릭, character 팜술 (alias Femme Soule).
  - Lesson: a producer that saw another's guess is not independent. The pilot "accepted" 퓨키 for Escape the Walls that way.
  - New-work characters stay "?" until a person confirms them. The WD tagger mis-named 팜술 as oma_kokichi.
  - The user decided on 2026-10-22: no reference-image matching step, because new-IP characters are few.
    - They are confirmed in 검수: db.add_human_fact or db.review_fact; a rejected fact stays rejected.
  - Schema rule: [[source-db-frozen-schema]].
- Intake (2026-11-03): `python -m lumina.sourcedb intake <Short folder> [files] [--dry-run]` moves OBS recordings from C:\Users\HETX\Videos into the folder (shutil.move, C: to D:), cuts what the DB lacks or cut at an older version, runs tagger/speech/embed, describes undescribed cuts (cap --max-cost $2), renames OBS-stamp files by first line, prints every new cut as a search line. Run it before searching; earlier edits lost most of their time doing this by hand (오미 session: 255 Bash calls).
  - On 2026-11-03 almost all media were still cuts@2 while CUTS_V is cuts@4 (uncommitted ingest.py change), so the first intake of a folder re-cuts it (오미: 7 min). 258 files under Project were not in the DB at all.
- Shot-plan search (2026-11-03, commit 4292746): MCP `source_plan` takes the whole cut plan (per shot: text, look = Korean picture description, seconds) and fuses BM25 with SigLIP text-to-picture (encoder kept alive in the analyzers env, ~15 s first call). Measured: picture descriptions as queries put the right cut in the top 8 of its work 77% (random 3%), raw narration 7%, so always write the look. New-IP characters often have no entity (오미): searched as text.
- Batch placement (2026-11-03, commit 6392e0e): `clip.place_shots` lays the whole cut plan on V1 in one undoable command (0.3 s edge margin, 100% or slowed to >=87%, exact ends, 115%/y1060 or 130% subject-centred from DB subject_x, all-or-nothing refusals); `get_frame_strip` shows many frames in one image. Needs the Lumina GUI restarted (commands run in the GUI's engine) and /mcp reconnect.
- Next: ingest real folders into ~/.lumina/source.db (import usage from past projects first), then the collection and edit-plan steps.

**Why:** user wants the whole pipeline automated with Claude only making small text decisions.
**How to apply:** keep heavy viewing/listening local; see [[autonomous-shorts-editing-goal]] for edit grammar and [[lumina-mcp-visible-work]].
