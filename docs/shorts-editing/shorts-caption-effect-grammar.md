---
name: shorts-caption-effect-grammar
description: "The user's Shorts caption animation and emphasis styles (pop / slam / fade, 120% size, name cards) and when each is used"
metadata:
  node_type: memory
  type: project
  originSessionId: 8b3fa121-2318-4ff0-bc75-804fc8bd03ba
  modified: 2026-10-22T13:01:00.264Z
---

Learned 2026-10-21 from 5,720 captions in 173 Shorts (Premiere, read-only). Captions are Pretendard-Bold at 100 px, white with a dark stroke, animated by a Transform effect.

- Pop (scale 70→100): the default, 58% of captions. Sentence starts and normal phrases; it carries the whoosh at sentence-start cuts.
- Slam (scale 180→100, 15%): emphasis. 64% of slams are also 120% size, 11% are 140%. Used mid-sentence on the key phrase, and in the hook (first 3 s); 85% get a sound.
- Fade + slide (opacity 0→100 with position, 24%): sentence tails, 67% of them ending a sentence. A calm read, usually with no sound.
- No animation + special style (black text, colored run, other fonts such as S-CoreDream-9Black, two lines): name or list cards and inserted titles.
- An annotation (a second, smaller layer about 48 px) adds side notes or the source; it shows up more often on emphasized captions.

**Why:** caption effects are part of step 5 and drive the SFX choice ([[shorts-sfx-grammar]]).
Track default is 100 px for all three tracks (the user's call, 2026-10-21). 120% on a slam is a per-cue override, not the default.

Rules the user set on 2026-10-21:
- An emphasized caption that is worth enlarging goes to 120 (per-cue font_size). It must still fit the 810 px box on one line. Measured: "독특한 컨셉으로" is 731 px at 120 and OK; "죽일 만큼 사랑하는" (856) and "엘프처럼 뾰족하게" (826) would wrap, so they stay at 100.
- TMI captions (furigana-like side notes) are Pretendard-Medium 48, same stroke and shadow, box 810×576, vertical center.
  - Above a 100 caption: y 680 (box top; center 968, just over the main text at 1055).
  - Above a 120 caption: y 670.
  - A more supplementary note goes below at y 960, split into lines with line breaks where it reads well ("odyk 캐릭터 디자이너\nAiobahn 뮤비\n나나히라 성우의 환상적인 조화").
  - In the Premiere edits the TMI is a second layer of the same graphic, so it animates with its caption.
- Lumina: TMI are graphic clips (text layers) on one ordinary video track named "TMI" (V4, below the title), not caption tracks. The user rejected three TMI caption tracks because they push the lower tracks out of view.
  - Several notes at the same time go into one graphic as separate layers (e.g. "Femme Soûle" at y 680 and "우연찮게도 ‘술’이라 부름" at y 960).
  - Each TMI graphic gets the same preset as its main caption as a clip effect, so they move together.
  - Main captions stay caption cues (no graphics conversion).
  - Verified 2026-10-21.

**Details (parsed 2026-10-21, 5,720 captions, 1,144 sentences)**
- Sentence template: pop at the start, slam on the focus phrase, fade on the last caption.
  - 3 captions: PSF 38%, PPF 38%.
  - 4 captions: PPSF 34%, PPPF 22%, PSPF 20%.
  - 5 captions: PPPSF 23%, PPSPF 21%, PPPPF 14%.
  - A sentence ending in F is followed by a sentence starting with P (772 of 955).
- Slam = the focus phrase just before the predicate:
  - 88% are mid-sentence and only 3% at the start. 42% sit on the second-to-last caption, and 55% are followed by the closing F.
  - They lean towards objects (을/를 15% vs 9% on pops) and subjects (이/가 15% vs 11%).
  - Count per sentence: one in 55% of sentences, none in 36%, two in 8%. Two only in long compound sentences, one per clause.
  - Slams never come back to back (826 single slams, 8 pairs). They are 11–19% of the captions in a Short.
  - Sentences with no slam are mostly the closing sentence or CTA (댓글), transitions, plain list items and questions. The first sentence almost always has a slam; slams are 21% of captions in the first 10% of the video.
- Fade is the end of a breath group, not only the sentence end. 32% of fades are mid-sentence, on clause endings (~고 ~며 ~서 ~면 ~는데 ~때문에: 39% vs 15% on pops).
  - After a mid-sentence fade there is a pause (38% vs 7% after a pop) and a cut (62% vs 20%), and the next caption pops.
  - Sentence ends without a fade: a slam on the final predicate as a reveal ("가짜입니다", "나체입니다").
  - Fades are 40% of captions in the last 10% of the video (ending and CTA).
- Size:
  - Slams: 120 (64%), 140 (11%), 100 (25%). A 100 slam is a long caption (7–9 characters vs 5–7) that would not fit at 120.
  - Pops at 120 (8% of pops): names or topics that open a point (은/는/이/가), more often in the first 10% of the video, with W3 or K2.
  - 140 (121 captions in 59 Shorts): very short, 3–6 characters, a punchline or key term ("가짜입니다", "말장난입니다", "PTSD가", "범부", "디저트에서"). Mostly slams with 뚜훅 or Guns; 24% in the first 10% of the video.
  - 200: a quoted single word or line ("“손”", "당장 하자!", "데라 씨...!").
  - 83: long captions shrunk to fit ("댓글로 알려주세요!", "화제가 되고 있습니다").
  - Mixed size (40 captions): the name or term at 120/140 and its particle or copula at 100, e.g. 토우지[140]+도, 오노 켄쇼[120]+와, 땅따먹기[140]+입니다.
- Current practice (user, 2026-10-21, corrected): emphasis is 120 only. 140 and font changes are both rare now. They are allowed as exceptions, e.g. a special font for a quoted line or technique name, or 140 for the one big reveal. Default: Pretendard-Bold, with 120 on the slammed focus phrase. The 140/200 and font stats below describe older edits.
- Fonts:
  - Pretendard-Bold: everything else.
  - GangwonEduSaeeumMedium (handwriting, 22 in 16 Shorts): inner monologue, the imagined viewer's reaction or a character's line ("어디서 본 것 같은데...?", "이걸 애니로 만든다고?", "굳이 내가 해야 하나...?"). Pop or slam.
  - 산돌제비 (SD Jebi; "SDJebib-eBd" and "SanSwB" are the same family, the user confirmed 2026-10-21): the display font for a named thing or a line, 20 captions in 13 Shorts, almost always slammed at 1.4–2.0×.
    - Technique or skill names: "경화수월", "불의유희", "재계상", and English names run one per caption ("Eight-Handled Sword" → "Divergent Sila" → …, K1 each).
    - Quoted lines and catchphrases: "“알아”", "죽이겠다...!를", "\"계획대로다!\"를", "당장 하자!", "데라 씨...!".
    - The big reveal of a Short: "남자입니다", "가짜입니다", "아르센 뤼팽".
    - About half are black text with a white stroke, at 1.8–2.0×; the rest white with the usual dark stroke. Sounds: 뚜훅 on reveals, Guns/Cash on names, K1 on English-name runs.
  - S-CoreDream-6Bold at about 36: image credits ("pixiv #…", "@…").
  - List cards (S-CoreDream-9Black, black text with a white stroke, no background, no animation, 0.6–1.2 s each, K2): name at 120 and the work/source as a 48 run or a second line ("료멘 스쿠나vs 고죠 사토루" + "주술회전", "노 게임 노 라이프" + "(2014)").

**User corrections on the 엣지러너 2 Short (2026-10-22).** The user re-edited my caption split in the GUI. These rules override the statistics above where they disagree.

- **Mid-sentence fade only on a coordinate clause.** Kept as a fade: "주역 대부분이 살아남았고," which states one fact before the next.
  - A clause that sets up what follows stays a pop. The user moved these from fade to pop: "더 슬플 거라더니" (contrast), "준비해 놓고도" (concession), "말을 아꼈다면" (condition).
  - The pause cannot tell the two apart. TTS pauses are about 0.3 s after every clause end. Decide by the connective's meaning.
- **A sentence-final reveal slam is only for a short punch predicate** ("가짜입니다", "나체입니다").
  - A long final predicate closes with a fade. The user changed "희망적이었습니다" (8 syllables) from slam to fade, even though it was the twist.
- **Merge a too-short caption into the next one.** "제가" (spoken 0.18 s) became "제가 생각하는 문제는". "그런데" (0.30 s) stayed on its own. The threshold is roughly 0.25 s spoken.
- **Drop the caption of a line that only says the work's name already shown in the main title.**
  - On the user's instruction: "바로" stays as its own caption, and "사이버펑크 엣지러너 2인데요" got no caption.
- **Confirmed unchanged:** five slams, each on the focus phrase right before the predicate: 실망을 안긴 / 휴지를 / 주역 대부분이 / 너무 큰소리를 / 큰 비극을 기대하게.
  - 120 only where it fits one line (810 px). "큰 비극을 기대하게" stays at 100. The closing sentence has no slam.

**How to apply:** pop by default, slam the one key phrase of a sentence, fade the sentence tail, and use card style for names in a list. Apply the 2026-10-22 corrections on top. Related: [[autonomous-shorts-editing-goal]], [[shorts-edit-review-lessons]].
