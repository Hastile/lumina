---
name: shorts-copy-grammar
description: "How the user's Shorts title copy (sub line + main line) is written — structure, endings, lengths — learned from 176 real copies"
metadata:
  node_type: memory
  type: project
  originSessionId: 8b3fa121-2318-4ff0-bc75-804fc8bd03ba
  modified: 2026-10-20T14:54:42.238Z
---

Learned 2026-10-20 from 176 sub/main pairs in "쇼츠 타이틀" graphics across D:\유튜브 채널\Project\**\*.prproj (read-only via lumina.io.prproj.extract).

- Sub + main read as ONE noun phrase: sub = modifier/hook, main = head noun (the topic).
  - ~47% 관형형 sub → noun-phrase main ("공포물보다 무섭다는 / FX전사 쿠루미")
  - ~9% subject sub (-가/-는/-의) → "~이유" or question main ("쇼우 터커가 / 최악의 악역인 이유")
  - ~13% noun phrase → noun phrase apposition ("최강의 경마 애니 / 죠죠 스틸 볼 런")
  - ~7% quote/exclamation/question sub → noun-phrase main ("\"스콜피온 테일!\" / 샤울라와 전갈")
- Sub almost never ends a sentence (5/176). Hearsay "-다는/-라는" is common. Colloquial meme tone (그 잡채, 감다살, 킹받게).
- Main ends in a noun: noun phrase 60%, "~들" 14%, "~이유" 10%, question 9%. The work/character name usually sits in main (83 main-only vs 36 sub-only).
- Lines break only at phrase boundaries.
- Length: sub median 8 Hangul (6–10), main median 7 (5–9); none overflowed, 9% came within 50px of the edge, so the fit check uses margin 0 (hard limits sub 11 / main 9).
- "?" masking is a deliberate reveal effect (user confirmed 2026-10-21). Seen in 11 of the sequences: the title opens with the payoff word as "?" (work/character name or twist noun, e.g. "?????을 파쿠리한 / 옷코츠 유타", "메이가 ??인 이유") and cuts to the full copy exactly at the start of the caption where the narration first says that word. Only one word group is hidden, particles stay visible, one "?" per syllable. Lumina: `shorts_set_copy` with `hide` + `reveal_at` (commit 1a2f96a).

**Why:** Claude is to write the copy itself in the automation pipeline.
**How to apply:** write copy in this grammar, then check it with Lumina `shorts.set_copy` dry_run. Related: [[shorts-automation-pipeline]], [[autonomous-shorts-editing-goal]].
