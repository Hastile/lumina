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
