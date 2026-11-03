---
name: shorts-tmi-grammar
description: "What the user puts in TMI side notes on Shorts captions: what would slow the narration if spoken, as labels or asides in a non-narration register"
metadata:
  node_type: memory
  type: project
  originSessionId: 8b3fa121-2318-4ff0-bc75-804fc8bd03ba
  modified: 2026-10-27T17:27:52.817Z
---

User, 2026-10-21: TMI is literally TMI. These are things that would drag the flow if the narration said all of them.

Parsed 2026-10-21: 604 TMI layers in 146 of 178 Shorts (median 4 per Short). They are spread through the middle of the video, rarely in the first or last 10%.

**What goes in a TMI** (81% add words that the narration never says):
- Identity label for something the narration only describes (most common): the voice actor's name on "성우는 여자입니다" (와카야마 시온), the work on a character name (원피스), the technique's name (만해 관음개홍희개), the creator (마이클 폰드스미스).
- Original script or spelling of a term the narration says: 緑(みどり), Boogie-Woogie, 夏油傑, ガブッと.
- The exact figure or a correction: "정확히는 3,852명", "약 300분 (애니 15화 분량)", "엄밀히는 원안이 맞는데…".
- Lore or mechanism detail behind a claim, memo style, often 2 lines below: "계활을 외치면 1분간 / 몸을 내어줄 것", "실제로 1화 공개 후 / 프리렌 2기를 제치고 화제가 됐음".
- The narrator's own aside or joke: "나루토 달리기 뭔데...", "흉측한 비주얼은 덤으로...", "콜라보 고증 확실하네 ㅋㅋ".
- Spoiler warnings ("스포 주의!") and small lists ("트리니티 : 둥근 실루엣 / 게헨나 : 뾰족한 실루엣").

**Form**
- Default to one TMI attached to one caption cue, with the same start and end. Spanning two or more main captions is uncommon and should be an exception, not a way to give a long note more reading time (user, 2026-10-29: "tmi가 자막 2개 이상 걸치는 경우 웬만하면 잘 없음"). The coronavirus/mask aside belongs only to "인상을 줍니다", not the whole "차갑고 / 위협적인 / 인상을 줍니다" phrase. Follow a user-specified cue exactly.
- Never in the narration's polite register (0% end in 니다/요/죠).
  - 76% are noun phrases or labels.
  - 12% are asides ending in ... ? ! ㅋ.
  - 8% are memo endings (~음 ~임 ~함 ~것); 4% plain speech.
- Mostly one line (84%), 7–19 characters. Pretendard-Medium 48.
- Labels, original names and asides go above the caption (y 680; y 670 over a 120 caption). Longer explanations and memos go below (y 960), split into 2–3 lines.
- A TMI hangs on the caption that mentions its referent: the noun or role it names, the claim it details.

**User correction (오미 Short, 2026-10-25).** I put "일본명 카스미" on "포켓몬스터의 투지가" and the user removed it: "카스미는 중요하지 않음". They added instead:
- the name of the gear the narration only describes: "고릴라 암즈" on "기계 팔을 하고 있어";
- the concrete look behind a vague word: "가슴이 트인 클리비지룩" on "상당히 여성스러운";
- a jab at a rival character: "AI 좋아 청년..." over "끝까지 카프리에게".
Original names and spellings are still valid TMI ("원어명 완전배제 아님", 2026-10-25). The test is whether the note matters to this Short. 카스미 added nothing to a passing comparison; notes about the subject and its story did.

**User corrections (사스케 닮은꼴, 2026-10-28).** I offered several TMI candidates and the user picked two.
- The original term "퀸시 (멸각사)" went over "일족의".
- The memo on "적 진영에 합류해" became "유하바하의 후계자로 지명됐었음".
  - Its tense is past because the story later reverses it: "지명됐었음으로 과거로 빠지는게 나을듯? 나중에는 유하바하 토벌하니까". A fact the story later undoes is written as past (~었음), so fans don't read it as current.
  - I placed this memo below the caption by its type (y 960). The user moved it above (y 680): "3번 tmi 위로 넣자". It is a one-line memo of 15 characters, so the below-caption slot is for genuinely long 2–3 line notes. Short one-liners go above, matching the other TMI in the Short.
- Pretendard has no kanji: 滅却師 rendered as empty boxes. Write the Korean reading (멸각사), or check that the font has the glyphs and look at a frame.

**Why:** Claude writes TMI in the edit step from the script and research.
**How to apply:** while writing the narration, move names, original spellings, exact numbers, lore and side jokes out of it into TMI on the caption that mentions them. Keep them short, never polite-register, and place them by type. See [[shorts-caption-effect-grammar]] for placement and styling.
