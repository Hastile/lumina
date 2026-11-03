---
name: shorts-edit-review-lessons
description: "What the user corrected while confirming the first automated Short (퓨키 데모 취소, 2026-10-22): sources, cut rhythm, framing, SFX, TMI"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 8b3fa121-2318-4ff0-bc75-804fc8bd03ba
  modified: 2026-10-27T14:54:59.613Z
---

From the review of the first automated Short, 퓨키 데모 취소 (PUKEY GODDESS SHOT TRICK folder), which the user approved for upload on 2026-10-22. Each rule is something the user corrected or changed by hand.

**Sources**
- Never build a Short from one kind of source; the first version "looked single-source".
- Mix footage (trailers, PV, MV) with the images in the project folder: fan art, milestone illustrations, design sheets.
- Fetch outside evidence too:
  - X posts through api.fxtwitter.com or yt-dlp;
  - images in news articles;
  - browser captures of store or search pages.
- Tell the user what is still missing.
- A claim about a trend needs a real case on screen. For AI clones: the original vs. the copy as a stacked split screen, with a TMI holding only the original's name at the top of the upper panel.
- Every picture must tie to what the caption says. Art from an unrelated game (ReVamp) felt 뜬금없 and was removed.
- Literal or comic matches land:
  - a line about indie games not getting made → a Roblox search full of copies;
  - the last line → the character throwing up.
- A line about players trying a demo needs footage that looks like real play: UI, cards, the player's hands.

- When the script refers to an earlier season, look for that season's shots inside the new season's official teasers before asking for a recording. The user pointed out on 2026-10-22 that the 엣지러너 2 Official Teaser (jjhRSNkquOc) opens with a 1편 recap, 0–21 s: Lucy and David, Lucy's blood tears, David's death, then "DAVID'S DEAD."
- Do not use a promo's text-only card ("DAVID'S DEAD." on a plain background) as a shot.
  - The user rejected the card itself, not only its position (2026-10-22). Show the scene it refers to instead.
  - Burned-in text over real footage ("NIGHT CITY WILL BURN AGAIN" over a burning street) was kept. It was scaled down and moved up so it reads above the caption.
- Teasers put black frames (fades) of about 0.3–0.5 s between flash shots. Measure brightness (signalstats YAVG; about 21 here = black) and take only the bright spans. Never run one source range across the teaser's own cuts.

**Framing**
- Pick shots where the character reads clearly.
- When she is off-centre, push to 130% and shift x to centre her.
- Avoid shots under the caption that are busy with lyric typography or nearly black.

**Cut rhythm**
- Not one caption = one cut. Shots span 2–4 captions where the phrase is one unit. The user merged these by hand:
  - "돌연 취소하면서 아쉬움을 남겼습니다" into one shot;
  - "하지만 아직 이름을 알리지 못한 인디게임들은 사정이 다르죠" into one 4 s shot.
- "융융 전파 신드롬을 만들었던 제작진이 참여했고" is one source (one illustration with a pan).
- The opening name line is one smooth shot (a slow zoom on one image). A 0.7 s first cut felt like the frames jumped.
- When a source shot fades in from black, start right after the fade completes; measure the brightness (signalstats YAVG) to find the frame.
- No sliver of another shot inside a clip near its edges: check the source's own scene cuts and keep at least 0.3 s.

**Comparison Shorts** (사스케 닮은꼴, 2026-10-27)
- A section about a look-alike shows only that character. When the narration names the original ("사스케가 생각나는데요"), do not cut to it. The user said "사스케 장면 쓰지 말 것" after I put Sasuke on that line inside Uryu's section.

**SFX** (the user's own changes)
- A name of an earlier work gets the identity hit: the user moved Hit Guns onto "융융 전파 신드롬을".
- Bells 2 goes on lines that build toward something: "먼저 나올 수 있다는" and "이미 유명해진".
- "50만 건을" takes Epic Hit 3, pre-rolled so the hit lands on the caption.
- Do not put Kick 2 on every still image; the user removed one.

**TMI**
- TMI must read at the narration's tempo. A long TMI at a fast sentence start was rejected ("템포상 처음에 읽히기 어려움"), as was one about an unrelated subject.
- When the user supplies a fact, use their wording ("Alliance Arts 개발 및 유통 똑같음").

**Why:** these are the gaps between the grammar statistics and what the user accepts.

**How to apply:** check every edit against this list before showing it. Related: [[shorts-cut-grammar]], [[shorts-sfx-grammar]], [[shorts-tmi-grammar]], [[shorts-caption-effect-grammar]], [[lumina-replace-keep-neighbors]], [[shorts-upload-title-grammar]].
