---
name: shorts-sfx-grammar
description: "Which SFX the user puts on which caption in Shorts, per sound and variant number, learned from 2,656 SFX on 5,720 captions in 173 Shorts"
metadata:
  node_type: memory
  type: project
  originSessionId: 8b3fa121-2318-4ff0-bc75-804fc8bd03ba
  modified: 2026-10-27T16:02:07.092Z
---

Learned 2026-10-21, read-only, from every Shorts sequence (has a "쇼츠 타이틀" clip) in D:\유튜브 채널\Project. Captions were joined with caption style and animation, picture changes, narration pauses (A1 envelope) and Kiwi tags. About 15 sounds per Short; 44% of captions get one.

**Hard timing rules**
- A sound starts on a caption's first frame. Epic Hit is pre-rolled about 0.7 s so its hit lands there.
- One sound per caption, at least 0.5 s apart.
- Tracks: A2/A3 main sounds, A4 bells and 물방울.

**The caption effect decides most of it** (see [[shorts-caption-effect-grammar]])
- Slam caption (scale 180→100, usually 120% size): 85% get a sound — a hit 44%, 뚜훅 25%, 티딩 ~10%, bells 5–8%.
- Pop caption (70→100) at a sentence start on a new shot: 68% get a whoosh.
- Fade/slide caption (a sentence tail, usually ending in EF): 83% get nothing. Exceptions:
  - a question gets Bells;
  - a letdown or negation gets 물방울.
- Name or list cards (no animation, black text, colored run, other font, two lines): Kick 2 49–81%.

**Per sound and number**
- Whoosh: sentence start + new footage + a narration pause of 0.15 s or more. Never on a sentence tail.
  - W3: frame 0 only.
  - W4: the structural whoosh — the body opener right after the hook, and the conclusion or outro (45% of W4s fall in the last two sentences). Favored after 그래서/따라서 and 즉.
  - W1, W5, W6, W7: rotation in the body. W1 and W6 lean to contrast or addition openers (그런데, 하지만, 또, 게다가). W5 has a long swell and goes on longer stretches. W7 is rare.
  - The same file is never used twice in a row. Mid-sentence cuts usually get nothing.
- Kick 2: list items, name cards and inserted images (the card style above). The same K2 repeats on every item. In a list of character pairs it is stacked with 빨강 파랑.wav.
- Kick 1: a light point inside an explanation — a definition, a translation or a term ("달의 송곳니", "Eight-Handled Sword", "계단을 오르는"). Pop or slam; often the first emphasis in a Short.
- Kick 5: a summarizing evaluation late in the video ("작품의 개성이 되었죠", "영화에도 진심인 애니라", "단 7화로").
- H:Guns: an identity, name or nickname reveal at the payoff of a sentence ("HOT LIMIT입니다", "이즈미 커티스를", "대포 주술사라는"). Early (position ≈0.35).
- H:Cash: a surprising action, event or absurd fact ("남자라는 것입니다", "이걸 애니로 만든다고?", "돌로 만드는 사고를"). Early to middle; the most slam-heavy (79%).
- H:Impactor: a twist or knockout blow, mostly in the second half ("모든 사건의 배후였다는", "키메라의 재료가 되었다는", "개차반", "똑같다는 겁니다").
- Epic Hit 3: the turning-point word inside an explanation, which builds curiosity ("현대 주술사가 (아니라는)", "다른 점이", "바로 생각나는"). It is used more in the last sentence. E4 and E2 are rare variants of it.
- Bells 2: the curiosity gap — the hook question's ending ("무슨 뜻일까요?", "들어보셨나요?"), "이유가 있습니다", or a fact announced to be explained next. Mostly the sentence end, on fade or pop captions.
- 뚜훅:
  - In the first 3 s, on the slammed key word of the hook question ("스바루가 【기억을 잃은】 이유는 무엇일까요?").
  - Later, on an odd or twisted fact ("유타는 후지와라가 【아닙니다】", "【노리고 만든】 캐릭터").
- 티딩:
  - At the end: the CTA ("댓글로 알려주세요!") or the closing verdict.
  - In the middle (≈6–18 s): the "aha" answer or trivia payoff ("원조가 있습니다!", "바로 화면 비율입니다", "이제는 도로롱으로").
- 난감한 물방울: a letdown, negation or awkwardness, usually a fade tail in 죠/네요 ("아쉽네요", "잘 와닿지 않습니다", "깜빡 속았어요"). Negation words appear in its captions 8× more often than elsewhere.
- Rare sounds:
  - SCH Button 5: an "item/variant" mention ("다양한 옷을", "말장난입니다").
  - 신서유기 or 짜잔: comic reveals.
  - H:Deep: a heavy symbolic statement.
  - Real voice or OST clips from the source go on A1.
- Opening pattern: W3 (frame 0) → 뚜훅 (slammed hook word) → B2 (question end) → W4 (body).

**Volume (user correction, 2026-11-03): levels come from Auto-Match, never from lowering clip Volume.** "클립볼륨을 떨구는게 아니라 오토매치 하라니까?" Do not set clip Volume -3 dB on 티딩, Kick 5 or Epic Hit 2, and do not hand-set 티딩's gain: tag every sound SFX and Auto-Match A2 and A3 (티딩 included). A4 (Bells 2, 물방울) stays out of the match. This replaces the clip-Volume rule of 2026-10-21 below, kept as history.

**Volume (user, 2026-10-21; clip Volume part replaced 2026-11-03)**
- Strong sounds get clip Volume -3 dB; the confirmed list is below.
- In Premiere only A2 and A3 are selected for Loudness Auto-Match.
- Bells 2 and 난감한 물방울 sit on A4 so they stay out of the match: Premiere measures their short sound on integrated loudness and boosts them about +6 dB (measured: B2 +5.9, 물방울 +6.2 at the -21 LUFS SFX target).
- Lumina's audio.auto_match measures clips with under 1 s of sound on momentary max, so it gives only +1.3 / -0.9 dB there, which is fine.
- 티딩 is also under 1 s. Lumina gives it -11.0 dB, where the user's Premiere projects hold -7.0. I set -7.0 by hand on the 엣지러너 2 Short (2026-10-22).
- The other gains matched the archive: 뚜훅 -6.4, Impactor -6.1, Kick 5 -7.9, whooshes -0.6 to -3.2.
- Volume -3 is volume_db, which Auto-Match (gain_db) leaves alone, so it survives the match.
- What the past projects actually hold (Premiere import reads levels since commit af5765f): no clip Volume changes on any SFX, Kick 5 and 티딩 included.
  - Track faders are A2/A3/A4 at -3 dB (170 Shorts) and A5 (BGM) at -6 dB.
  - The Audio Gain is pure Auto-Match: K2 -5.4, Kick 5 -7.8, 티딩 -7.0, 뚜훅 -6.4, Whoosh -0.6 to -3.2, Impactor -6.1.
  - Bells and 물방울 have gain 0 everywhere, even the few on A2/A3.
- Rule confirmed by the user (2026-10-21): both apply.
  - Track faders: A2, A3 and A4 at -3 dB, the BGM track (A5) at -6 dB.
  - Clip Volume -3 dB on exactly 티딩, Kick 5 and Epic Hit 2 ("일단 이 3개"; there may be more later). This is newer than the archived projects.
  - Auto-Match goes on A2 and A3 only; A4 (Bells 2, 물방울) stays untouched.

**BGM (user correction, 2026-11-03): BGM is levelled in LUFS by Auto-Match too ("bgm 도 lufs지"), target -24 to -27 LUFS ("목표 db 는 -24~-27 사이").** Tag it Music and Auto-Match it to -25.5 LUFS (`shorts_set_bgm`), with or without narration over it; the clip volume stays 0. The meter-reading procedure below is history, replaced by this.

**BGM (user, 2026-10-22; replaced 2026-11-03)**
- BGM must move between -24 and -27 dB on the track meter, measured post-fader on the BGM alone.
- The user corrected this: it is meter dB (peak), not LUFS. -25 LUFS is about 8 dB too loud.
- It sits on A5, whose track fader is -6 dB; set the clip's Audio Gain.
- Check it by soloing A5, exporting a wav and simulating the meter (40 ms peak, 24 dB/s fall). Aim for a median of about -25.5 dB.
- Pick a stretch of the track with an even level.
- 퓨키 example: the Yukari OST from 114 s with gain -14.4 dB on top of the -6 fader reads 10th–90th percentile -27.7 to -23.9 dB, median -25.5.

The Guns/Cash/Impactor/E3 split comes from reading sentences, not hard features; treat it as a tendency. Clip volumes were not captured (the extractor drops audio levels).

**Stay inside this palette (user, 2026-10-28).** On the 사스케 닮은꼴 Short I put DEOKDI_SFX_PROJECT magic sounds (Spirit Appearance, Magical Shimmer) on a Ransōtengai scene. The user said "내가 쓰던 sfx가 아니여서 어색함". I replaced them with W5 by the pop/new-shot rule. Even when a scene seems to call for a themed sound (magic, energy), pick from the sounds above.

**Why:** Claude places the SFX itself in the edit step ([[autonomous-shorts-editing-goal]]).
**How to apply:** go caption by caption. Pick the caption effect first ([[shorts-caption-effect-grammar]]), then the sound by the rules above; snap it to the caption start and rotate the whoosh variants. Related: [[shorts-copy-grammar]], [[shorts-automation-pipeline]].
