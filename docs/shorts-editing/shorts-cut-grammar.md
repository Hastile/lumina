---
name: shorts-cut-grammar
description: "How the user cuts footage in Shorts (lengths, cuts on caption starts, speed) and the no-flash-frame rule"
metadata:
  node_type: memory
  type: project
  originSessionId: 8b3fa121-2318-4ff0-bc75-804fc8bd03ba
  modified: 2026-10-27T17:33:11.897Z
---

Learned 2026-10-21 from 2,221 footage clips in 177 Shorts (Premiere, read-only). Scene changes were detected with ffmpeg (scene score 0.3) and counted in timeline frames, because the timeline samples the source once per frame.

**User correction (2026-10-28): "모든 컷에 키프레임 주면 그건 슬라이드쇼지".**
- Do not answer "the cuts feel static" by adding zoom/position keyframes to every shot. Choose source ranges with actual expressions, gestures and actions first.
- Keep shot framing static by default. Add a camera move only when that particular shot needs it, such as keeping a moving hand visible; stop tracking when the subject stops.
- An across-the-board push/pull treatment is not a substitute for better footage selection or cut allocation.

**Rule the user states: cuts must never jump ("컷이 튀지 않게 하는 건 기본").**
- No frame of a neighbouring shot at a clip's edge: an in point a few frames before a source scene change shows the previous shot for 1–2 frames.
- Their own edits still have this at about 3–4% of clip starts (5 of 8 flagged starts were real when looked at; the rest were anime motion or flashes read as cuts). Clip ends are cleaner (92% clean).
- Automation must reach zero: put an edge exactly on the scene change, or at least ~3 timeline frames away from it. Judge it in timeline frames: a change less than one timeline frame inside never shows.
- Real jump cuts within one shot (skipping ≤1 s of the same shot with no scene change) are rare: 33 of 2,042 cuts.

**Grammar**
- About 12 footage clips per Short; median 2.0 s (10–90%: 0.9–4.0 s); 12% under 1 s, 4% over 5 s.
- 88% of cuts land on a caption start.
- 86% of clips are on V1; V2–V3 hold overlays.
- 25% of clips have a changed speed (see [[shorts-marker-workflow]]: the marked moment is put on a caption start).
- Back-to-back clips of the same file mostly skip ahead to another moment (>3 s in 343 of 535 cases), not to the next frame.

**Framing and motion** (2,545 picture clips: 2,221 footage, 324 stills)
- 1920×1080 footage sits at position (540, 1060) at **115%** (on screen y 439–1681) or **130%** (y 358–1762). It is cropped left and right; the whole height shows; the title fills the black band above.
  - The template's V5 graphic has black bands at y 0–481 and 1675–1920, so footage shows only in the window y 481–1675.
  - Centred at y 1060, covering that window needs ≥113.9%. **115% is the smallest round scale with no gap at the bands**; 130% zooms in further (831 vs 939 source px wide).
  - Scale is per-shot framing (the user pointed out that some clips are zoomed in even further). Effective scale for 1920-wide 16:9 footage:
    - 114–120%: 47%, the base that fills the window.
    - 121–135%: 35% (130).
    - 136–200%: 9%. 28% of these are also moved sideways ±100–800 px to centre the subject.
    - Not by era, not to hide letterbox or burned-in text (tested). The same file is re-zoomed within a Short 17% of the time.
  - Below 114% (10%) is a deliberate layout, never a gap at the bands:
    - Stacked split screen for comparisons: two ~58% panels at y≈770 and y≈1380 inside the window.
    - Or a small picture-in-picture on V2/V4.
  - Automation needs the subject's (face's) position and size per shot, so the footage DB should detect them. Then: 115% floor, push in until the subject reads, and shift x to centre it.
- Footage is rarely animated (4%): pans left/right over ~2 s, or zoom-in ×1.15–1.35 over ~2 s. Effects are almost never used (Transform 12, Gaussian blur 2); transitions ~0.
- 61% of footage starts get a sound (whooshes, see [[shorts-sfx-grammar]]).
- Stills appear in 55% of Shorts, about 3 per Short. They are shorter than footage (0.8–1.9 s) and 91% start on a caption start.
  - 64% are on V1, full frame (fill or between fit and fill).
  - 28% are on V2 as an inset smaller than fit, over the footage.
  - 27% are animated: a slow zoom-in ×1.15 over ~2 s (Ken Burns), or a pan (mostly left or up) over ~2.4 s.
  - 75% get a sound on their start, mostly Kick 2.

Not analysed yet: which shot goes with which caption content (needs looking at the footage).

**End of a Short** (measured 2026-10-22 on 172 Shorts; the user's rule: "쇼츠 영상 끝은 나레이션보다 소폭 길며, 나머지 트랙도 끝 맞출 것")
- The video ends a little after the narration: median 6 frames (0.2 s), 3–9 frames typical.
- Footage, captions, the title and the band graphic all end on that same frame (171 of 172).
- Audio ends at or before it, so sound tails are cut there.
- The template's title and band graphics run to 60 s, so trim them to the end.

**Shot ↔ narration (user corrections, 2026-10-25, 오미 Short)**
- One phrase = one shot. Cutting on every caption ("컷 너무 잘게") and uneven bursts of short clips ("컷 배분 더럽게") both get rejected; a hook built from 3 clips of 0.7–2 s with fast source cuts inside was rejected too.
- An event shot goes only where the narration tells that event: the punch only on "주먹까지 날리지만", never borrowed for the hook ("펀치는 주먹을 날리는 대목에만"). The hook uses a clean single shot of the subject instead.
- When the copy holds the subject back for a reveal ("…조연이 있습니다 / 바로 오미인데요", title "??"), the subject's face must not show before the reveal line ("오미가 왜 0초부터 나오는데"). The user's own fix (2026-10-26) did NOT use other footage (they deleted the trailer clips): it used the subject's footage with the face hidden — far silhouette walking (1 caption), body close-up cut off at the neck (2 captions), then one shot starting on the caption before the reveal that pans from her hand onto her face, the face landing just after "바로 오미인데요" starts. A static shot there is "재미없어".
- The user names a source for a beat ("구해주는건 e09 0619") when the beat needs the real event (the rescue), even if the shot is dark/wide; push in (150%) on the action.

**Cut distribution in the user's own 오미 edit (2026-10-26, "컷을 어떻게 배분했는지 봐라")**
- 19 shots in 40.6 s, 1.2–3.1 s (mean 2.1); every cut on a caption start.
- Default = one shot per 2 captions (modifier + predicate: "그녀는 헝클어진 / 녹색 머리에", "야구모자를 / 쓰고 있어"); cut at clause ends (-있어 / -지만 / -고 / -는데요), never inside a clause.
- One event clause = one long shot of 3–4 captions (punch "복수에…날리지만" 2.8 s, rescue "그가…했습니다" 2.6 s, closing line 3.1 s).
- A subject switch gets its own short shot: "그런데 정작 D는" = 1 caption, 1.2 s of D; then "오미의 충고를…" and "끝까지 카프리에게…" each a new shot.
- Description sections stay in one source (3 consecutive 1912 shots for 민소매/터프/몸매); story sections change source per event.
- Speeds 0.87–1.05 stretch a shot to fill its clause exactly instead of adding a cut.
- An inset illustrating a word starts on that word's caption (투지 image moved from "포켓몬스터의" to "투지가 떠오를 만큼"), over a full-figure shot of the subject so the comparison reads.

**Repeat offence: 사스케 닮은꼴 (2026-10-28).** The rules above were already here, and I still placed messy cuts. The user had to step in three times, then said "편집문법 학습 좀 하고. 다음에도 컷 배분 더럽게 하지말고".
- **D section.** A lightning shot dissolved into a white frame, so I trimmed it and rate-stretched it to fill the gap. The user said "D 컷 깔끔하게 배분해라". The fix was one shot per 2 captions at 100% speed: a cleaner source range, not a stretched one.
- **Nickname line.** The user extended my hologram shot over all 4 captions of "아예 / 사이버 사스케라는 / 별명까지 / 붙었습니다" (2.8 s). They deleted the extra shot I had put inside the sentence.
- **Revenge sentence.** I filled "심지어 자신의 일족을 몰살한 범인을 찾아 복수하려 한다는 설정마저 사스케와 닮았죠" with 5–6 shots: village, emblem, bodies, corpse, embers, eye. The user cleared it all and set markers for exactly 3 shots:
  - 3 captions: setup clause, bodies in fire;
  - 2 captions: "복수하려 / 한다는 설정마저", embers face;
  - 1 caption: fade tail "사스케와 닮았죠", eye close-up.
- **CTA.** I stretched the last clip to 85% to hide a jump. The user re-cut it at 100% from another source in point.

The approved result (20 shots, 37 s) shows what a clean distribution looks like:
- Every cut is on a caption start. Shots run 1.13–3.47 s; none is under 1.1 s.
- Captions per shot: 2,2,2,2,2,2,1,2,1,1,3,4,2,2,4,3,2,1,3,2.
- A 1-caption shot is only a full clause of ≥1.1 s ("친구라는 관계부터", "적 진영에 합류해", the closing fade).
- A short lead-in caption ("그리고", "아예", "심지어", 0.4 s) never stands alone; it rides with the next caption's shot.
- A punchline sentence is one long shot: "심지어 일본판 성우도 스기야마 노리아키로 똑같죠" = 1 shot, 3.5 s; the nickname line = 1 shot, 2.8 s.
- Speed is 100% except one 84.6% shot, placed on the user's own marker.

**Hold or cut at each caption boundary (archive, 2026-10-28).**
- Data: 180 unique Shorts from 230 .prproj (read-only), 5,590 caption-to-caption boundaries.
- A boundary counts as a cut when the topmost picture on V1–V3 changes within 2.5 frames of the next caption's start.
- 87% of picture changes land on a caption start. Captions run back to back (pause < 0.05 s at 99% of boundaries), so pauses don't decide cuts.
- **The sentence decides, not the clock.**
  - **Sentence end** (the previous caption fades, or ends in 다/요/죠/까/?): cut 83%. After a fade on a sentence ending: 96%.
  - The only hold at a sentence end is when the shot is still under 0.7 s (cut 31%).
  - If the next sentence opens with a slam: 50%.
  - **Inside a sentence: hold.** Cut 19% overall.
    - Phrase-ending captions: 12–24% at any shot age, even past 3 s. Long sentences are not split for length; the sentence's length is the shot's length.
    - Clause ends (-고 -지만 -는데 -면 -며 -서): 12% if the shot is under 1 s, 41% at 1–2 s, 53% at ≥2 s. This is the one place a long sentence takes a second shot.
  - **Never cut onto the sentence tail.** Next caption is a fade: cut 10%.
  - Don't split a modifier from its head or an object from its verb: after captions ending in 의 / 을를 / adnominal -ㄴ/-는, cut 12–14%.
  - **Mid-sentence cuts happen when a new thing must be shown**, not for rhythm:
    - a list item (after 와/나: 31%);
    - a name reveal ("진격의 거인으로 유명한 → WIT 스튜디오입니다");
    - a subject switch ("명조의 린네나 → 니케의 티아를 보면").
- A sentence averages ~3.9 captions and ~0.55 mid-sentence cuts, so shots come out at ~2–2.5 captions on average. "One shot per 2 captions" is that average, not a rule to apply per pair.
- Scripts: `D:\Dev\lumina\docs\shorts-editing\cut_grammar_extract.py` and `cut_grammar_analyze.py` (run with `uv run --project engine python`). They use `lumina.io.prproj.extract`; captions = text graphics on V4+ centred at y 900–1250, effect taken from Transform keyframes.

**Plan the cuts before placing anything.** Write the plan, check it, then place:
1. List the captions with start frames. Group them into shots by sentence, using the hold/cut rules above:
   - one sentence = one shot by default;
   - add a cut inside a sentence only at a clause end once the shot is ≥1 s, or where a new thing must be shown (list item, name reveal, subject switch);
   - never cut right before the fade tail;
   - a lead-in caption under 0.5 s joins the next caption.
2. Reject the plan if any of these hold:
   - a shot is under 1.1 s;
   - a cut is not on a caption start;
   - a sentence has more shots than clauses;
   - more than one 1-caption shot comes in a row.
3. Pick sources per shot afterwards. If a source range has a flaw (white flash, dissolve, a jump), choose another range or source. Do not stretch it below 87% or trim it into a sliver.
4. After placing, read the V1 shot lengths back against the plan before showing the user.

**Why:** cut placement is the core of the edit step, and the user's baseline is that a cut must never jump. Messy distribution has now been corrected on two Shorts in a row (오미, 사스케 닮은꼴).
**How to apply:**
- Start cuts on caption starts, at 1.1–4 s.
- Plan the shot table first (above).
- Check every clip edge against the source's scene changes: snap the edge onto the change, or move it ≥3 frames away.

Related: [[autonomous-shorts-editing-goal]], [[shorts-sfx-grammar]], [[shorts-marker-workflow]], [[shorts-edit-review-lessons]].
