# Handoff: Shorts editing automation (2026-11-03)

Continue improving the Caddynote Shorts editing automation from here. Read `AGENTS.md`,
`docs/editing-grammar/README.md` and `docs/shorts-editing/README.md` first.

## Done in this round (commits on this branch)

| Commit | What |
|---|---|
| 851d028, ba88d41 | `python -m lumina.sourcedb intake <Short folder>`: moves OBS recordings in, cuts/analyses what the DB lacks, describes (cap `--max-cost`), lists new cuts. Recorder now moves across drives. |
| 4292746 | MCP `source_plan`: one call per cut plan; per shot `text` + `look` (Korean picture description) + `seconds`; BM25 fused with SigLIP text-to-picture (encoder kept alive, `engine/analyzers/text_encoder.py`). Work/character filters 7 s → 0.25 s. |
| 6392e0e | `clip.place_shots` (whole cut plan on V1, one undo, all-or-nothing) and MCP `get_frame_strip` (many frames in one image). |
| this commit | `engine/analyzers/eval_text_search.py`, this handoff, the shared grammar docs. |

Why: past Short sessions spent 2–3.5 h and 540–650 Lumina calls (`clip_place` 60–110,
`clip_set_property` 50–110, `get_frame_image` 100–175) plus hand `scdet`/contact sheets
for recordings missing from the DB.

Evidence for `look`: SigLIP recall@8 with past narration captions 7% (random 2%);
with picture descriptions 77% (pool 536) / 58% (pool 4816). Verified live: 오미 shots found
by look, `place_shots` 0.2 s, off-centre subject centred by DB `subject_x`.

## Done in the cloud session (2026-11-03)

| What | Where |
|---|---|
| `shorts_plan` (MCP, read-only): captions → shots (sentence grammar), pop/slam/fade per caption, sounds (W3/W4/rotation, hit?, Bells 2, 물방울, 티딩), grammar problems. The planning half of next steps 1–3. | `engine/lumina/shorts_plan.py` |
| `shorts.caption_effects`: all effects in one all-or-nothing edit; slams 120 only when they fit 810 px. Next step 2. | `engine/lumina/commands/shorts_fx.py` |
| `shorts.place_sfx`: sounds by name, grammar tracks, Epic Hit pre-roll, tails cut, sfx tag, A2/A3 Auto-Match (levels from Auto-Match only; no clip volume cuts, user 2026-11-03). Next step 3. | same |
| `shorts.build` (one command, one undo: place_shots → end at narration + 6 f → effects → sounds → check) and `shorts_check` (text-only review: shots, gaps, flashes/dark from the DB, ending, captions, sounds). | `engine/lumina/commands/shorts_fx.py`, `engine/lumina/shorts_check.py` |
| Calibration of the draft against the archive (precision/recall/F1 of cuts per boundary kind, effect confusion, worst Shorts): run `docs/shorts-editing/calibrate_plan.py` on the PC on `cut_grammar_extract.py` output. **First thing to run on the PC**: it says how good `shorts_plan` is. | `engine/lumina/shorts_calibrate.py` |
| `edit_check` (MCP; the same checker with `profile: longform`): reuse of a source span or near-duplicate media, caption completion rules (frame 0, gaps, trailing punctuation, protected splits, 3.5 s / 5 s / two sentences), cuts that split a phrase. Shorts get reuse and caption checks too. | `engine/lumina/shorts_check.py` |
| `shorts.add_tmi`: TMI notes on caption cues in one edit (V4/TMI track, cue timing, Pretendard-Medium 48, y 680/670/960, the caption track's stroke/shadow/effects; warnings for polite endings, long one-liners, missing glyphs). Verify on the PC that the copied caption effect animates the note with its caption. | `engine/lumina/commands/shorts_fx.py` |
| `LUMINA_TOOLS` tool profiles (shorts 58 tools / 57k chars, longform 111, core 42, all 154 / 136k). | `engine/lumina/mcp_server.py` |
| SFX calibration: `cut_grammar_extract.py` now records A2-A4 sounds; `calibrate_plan.py` reports sound precision/recall, same kind, same sound, and what the user had where the draft put each sound. **Re-run the extract** before calibrating. | `engine/lumina/shorts_calibrate.py` |
| `shorts.set_bgm`: BGM kept inside -27 to -24 LUFS by Auto-Match (user, 2026-11-03): left alone inside the band, matched to the nearer edge outside it, no midpoint; tagged Music, clip volume untouched. | `engine/lumina/commands/shorts_fx.py` |
| Reveals: a caption opening with 바로 / 그 정체는 / 정답은 / 주인공은 mid-sentence is a cut in the draft (`auto_reveal`); names from the DB's works/characters first said mid-sentence are marked `reveal?` only (look-alike sections must not cut to the original). | `engine/lumina/shorts_plan.py` |
| User rule (2026-11-03): do not raise the footage's y; it opens a gap above the lower band. Face behind the caption → another range or source. README and card updated. | docs |
| `docs/shorts-editing/CARD.md`: one page (6 KB) replacing ~143 KB of grammar reading per session; AGENTS.md/CLAUDE.md point at it first. | docs |
| `docs/shorts-editing/session_cost.py`: Lumina calls by tool, frames looked at, tokens and minutes per Claude Code session, to measure the gain. | docs |
| Source DB upkeep: `health` (read-only report with fixing commands), `backup`, `recut --stale`, `describe --misaligned`, `merge`. See `docs/shorts-editing/source-db-frozen-schema.md` "Upkeep". | `engine/lumina/sourcedb/health.py` |
| The SigLIP text encoder starts in the background when the MCP server starts (`LUMINA_WARM_ENCODER=0` turns it off). Next step 5. | `engine/lumina/mcp_server.py` |

Tested headless only (`tests/test_shorts_fx.py`). Still to verify on the PC: `shorts_plan` on a real Short against the user's own cut (e.g. 사스케 닮은꼴), sound names against the real SFX folders, and the 120-px fit with Pretendard installed.

## What a cloud session can and cannot do

Not available in the cloud: `~/.lumina/source.db` (55k cuts, 1.45M facts), footage on `D:\`,
the GPU analyzers env, the Lumina GUI, OBS. So: write code + headless tests
(`cd engine && uv run pytest`), design from the grammar docs. Anything that measures on the
DB or edits a project must run on the user's PC.

## Suggested next work (cloud-friendly first)

1. **Draft the cut plan from captions** (pure text logic): group SRT cues into shots by
   `docs/shorts-editing/shorts-cut-grammar.md` (sentence end = cut, hold inside a sentence,
   clause ends after ≥1 s, list items, name reveals, no shot < 1.1 s, merge < 0.5 s lead-ins).
   Output feeds `source_plan` / `clip.place_shots`. Test on caption fixtures.
2. **Caption effects in one command**: slam/fade/pop by `shorts-caption-effect-grammar.md`
   (move to V8/V9, 120 px), all-or-nothing like `place_shots`.
3. **SFX placement in one command** by `shorts-sfx-grammar.md` (A2–A4, Epic Hit 3 pre-roll
   21 f, `covered` check, audio tags).
4. Image framing in `place_shots` (pictures keep 100% now): derive a rule from `usage`
   scale/pos of past image clips — needs the DB, so ship the analysis script and let the user run it.
5. Start the SigLIP text encoder when the MCP server starts (first call costs ~15 s).

## Left on the user's PC

- Edgerunners 2 descriptions paused: 116 of 148 media done, ~32 left (~$4):
  `uv run python -m lumina.sourcedb intake "D:/유튜브 채널/Project/사이버펑크/엣지러너 2" --max-cost 20`.
  258 files under `Project` were not in the DB; nearly all media are `cuts@2`.
- The working tree holds uncommitted work NOT on this branch: `cuts@4` (dark hard cuts) in
  `sourcedb/ingest.py`, `source_review`, caption/speech tools, GUI changes; with it
  `tests/test_tracks.py::test_caption_add_edit_and_upgrade` fails. Do not assume it exists here.
- `clip.place_shots` needs the GUI restarted and `/mcp` reconnected to be usable.

## Rules that hold

Source DB schema frozen (new metadata = fact kinds / lexicon rows). Edit user projects only
through Lumina MCP, visibly; never rewrite `.lumina` files. Sources are moved, not copied.
Reference images of `docs/editing-grammar/references/dugong-evidence/` were left out of the
public repo (frames of another channel's video); they stay on the user's PC.
