"""shorts_plan: the Short's edit plan drafted from its captions, in one read-only call.

Planning a Short used to be done in the agent's head, caption by caption: group
the captions into shots, decide each caption's effect, then each sound. It is
slow, costs many tokens, and is where the edits went wrong ("컷 배분 더럽게").
The grammar in docs/shorts-editing is mostly mechanical, so this module applies
it and hands back a draft the agent only reviews:

  * shots: captions grouped by sentence (shorts-cut-grammar.md "Hold or cut at
    each caption boundary"): a sentence is one shot; a second shot only at a
    clause end once the shot has run 1 s, at a list item, or where the agent
    marks a reveal; never onto the sentence tail; lead-ins under 0.5 s ride with
    the next caption; nothing under 1.1 s. Each shot has its frames and the
    narration it covers, ready for source_plan (add ``look``) and
    clip.place_shots.
  * effects: pop / slam / fade per caption (shorts-caption-effect-grammar.md):
    pop by default, fade on the sentence tail and on coordinate clauses, one slam
    on the focus phrase before the predicate, never two slams in a row, none in
    the closing sentence.
  * sounds: W3 at frame 0, a whoosh on each new sentence that starts a shot
    (W4 for the body opener and the ending, otherwise W1/W5/W6 in rotation, never
    the same twice running), a hit on slams, Bells 2 on questions, 난감한 물방울
    on letdowns, 티딩 on the closing line (shorts-sfx-grammar.md). Hits are
    marked "choose" with the candidates, because the grammar picks them by
    meaning.

Korean is read from caption endings only (no tagger), so the draft is a
starting point: the agent fixes what reads wrong and passes the result on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from . import timebase

TPS = timebase.TICKS_PER_SECOND

# caption track name -> effect (the template's three caption tracks)
EFFECT_TRACK_WORDS = {"pop": ("커졌다", "pop"), "slam": ("꽝", "slam"), "fade": ("밑에서", "스무쓰", "fade")}

SENTENCE_END = ("다", "요", "죠", "까", "죵", "네", "군")
CLAUSE_END = ("고", "지만", "는데", "은데", "ㄴ데", "데", "면", "며", "서", "니까", "때문에", "더니", "어도", "아도", "고도", "려고",
              "아", "어", "해", "여", "워", "와서")  # -아/-어 connectives: 찾아, 합류해 (not -려: 하려 한다)
COORDINATE = ("고", "며", "으며")          # a fact, then the next one: a mid-sentence fade
SETUP = ("지만", "는데", "은데", "더니", "고도", "다면", "면", "려고", "니까")  # sets up what follows: stays a pop
BOUND = ("의", "을", "를")                # an object/modifier never ends a shot
LIST_JOIN = ("와", "과", "나", "이나", "랑", "이랑", "하고")
NEGATIVE = ("않", "못", "없", "아쉽", "안 ", "별로", "실망", "글쎄")
CTA = ("댓글", "구독", "좋아요", "알려주세요")

WHOOSH_BODY = ("W1", "W5", "W6")
HIT_CHOICES = ("뚜훅", "H:Cash", "H:Guns", "H:Impactor", "Kick 1", "Epic Hit 3")


@dataclass
class Cue:
    i: int
    start: int                    # ticks
    end: int
    text: str
    effect: Optional[str] = None  # current effect (from its track), if on a sequence
    clip_id: Optional[str] = None

    @property
    def seconds(self) -> float:
        return (self.end - self.start) / TPS


@dataclass
class Shot:
    cues: list[Cue]
    reason: str = ""

    @property
    def start(self) -> int:
        return self.cues[0].start

    @property
    def end(self) -> int:
        return self.cues[-1].end

    @property
    def seconds(self) -> float:
        return (self.end - self.start) / TPS

    @property
    def text(self) -> str:
        return " ".join(c.text for c in self.cues)


@dataclass
class Plan:
    cues: list[Cue]
    sentences: list[list[Cue]]
    shots: list[Shot]
    effects: dict[int, str] = field(default_factory=dict)     # cue index -> pop/slam/fade
    sounds: dict[int, str] = field(default_factory=dict)      # cue index -> sound name ("choose: ..." for hits)
    problems: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- reading Korean endings

def _core(text: str) -> str:
    """The caption without trailing punctuation, quotes and spaces."""
    return re.sub(r"[\s\"'”’)\]…~!?.,·:;]+$", "", text.strip())


def _ends(text: str, endings: tuple[str, ...]) -> bool:
    core = _core(text)
    if any(core.endswith(e) for e in endings if not e.startswith("ㄴ")):
        return True
    if "ㄴ데" in endings and core.endswith("데") and len(core) >= 2 and _jong(core[-2]) == 4:
        return True
    return False


def _jong(ch: str) -> int:
    """Final consonant index of a Hangul syllable (0 = none, 4 = ㄴ, 8 = ㄹ), -1 if not Hangul."""
    code = ord(ch) - 0xAC00
    return code % 28 if 0 <= code < 11172 else -1


def is_question(text: str) -> bool:
    return text.strip().endswith("?") or _core(text).endswith(("까", "나요", "가요", "을까요", "ㄹ까요"))


def is_sentence_end(text: str) -> bool:
    t = text.strip()
    if t.endswith(("?", "!", ".")):
        return True
    if t.endswith((",", "…", "...")):
        return False
    return _ends(t, SENTENCE_END) and not _ends(t, CLAUSE_END)


def is_clause_end(text: str) -> bool:
    t = text.strip()
    return t.endswith(",") or _ends(t, CLAUSE_END)


def is_bound(text: str) -> bool:
    """Ends on an object/genitive particle or an adnominal (-ㄴ/-ㄹ verb form): the next caption completes it."""
    core = _core(text)
    if not core or text.strip().endswith(","):
        return False
    if core.endswith(BOUND):
        return True
    last = core[-1]
    j = _jong(last)
    # adnominal -ㄴ/-ㄹ/-는/-던 on a verb (not the topic particles 은/는 after a noun-like word)
    return (j in (4, 8) and last not in "은는인을" and len(core) >= 2) or core.endswith(("하는", "되는", "있는", "없는", "던"))


def is_list_item(text: str) -> bool:
    core = _core(text)
    return core.endswith(LIST_JOIN) or text.strip().endswith(("와,", "과,"))


def hangul_len(text: str) -> int:
    return sum(1 for ch in text if _jong(ch) >= 0)


# ---------------------------------------------------------------- planning

def sentences_of(cues: list[Cue]) -> list[list[Cue]]:
    out, cur = [], []
    for c in cues:
        cur.append(c)
        if is_sentence_end(c.text):
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def _group_sentence(sent: list[Cue], reveal: set[int], min_shot: float, lead_in: float) -> list[Shot]:
    shots = [Shot([sent[0]], "sentence start")]
    for k in range(1, len(sent)):
        prev, cue = sent[k - 1], sent[k]
        age = (prev.end - shots[-1].start) / TPS
        tail = k == len(sent) - 1
        cut, why = False, ""
        if cue.i in reveal:
            cut, why = True, "reveal / subject switch"
        elif prev.seconds < lead_in or is_bound(prev.text) or (tail and len(sent) > 1):
            cut = False  # lead-in rides along; never split modifier/object from its head; never cut onto the tail
        elif is_clause_end(prev.text) and age >= 1.0:
            cut, why = True, "clause end"
        elif is_list_item(prev.text) and age >= min_shot:
            cut, why = True, "list item"
        if cut:
            shots.append(Shot([cue], why))
        else:
            shots[-1].cues.append(cue)
    return shots


def _merge_short(shots: list[Shot], min_shot: float) -> list[Shot]:
    """Fold shots under min_shot into a neighbour (the next one in the same sentence first)."""
    changed = True
    while changed and len(shots) > 1:
        changed = False
        for n, s in enumerate(shots):
            if s.seconds >= min_shot:
                continue
            if n + 1 < len(shots) and (n == 0 or shots[n + 1].seconds <= shots[n - 1].seconds):
                shots[n + 1] = Shot(s.cues + shots[n + 1].cues, s.reason)
            else:
                shots[n - 1].cues.extend(s.cues)
            del shots[n]
            changed = True
            break
    return shots


def plan_shots(cues: list[Cue], *, reveal: tuple[int, ...] = (), min_shot: float = 1.1, sentence_hold: float = 0.7,
               lead_in: float = 0.5) -> tuple[list[list[Cue]], list[Shot]]:
    sents = sentences_of(cues)
    shots: list[Shot] = []
    for sent in sents:
        part = _group_sentence(sent, set(reveal), min_shot, lead_in)
        if shots and shots[-1].seconds < sentence_hold and sent[0].i not in reveal:
            shots[-1].cues.extend(part[0].cues)  # a sentence end under 0.7 s holds into the next sentence
            part = part[1:]
        shots.extend(part)
    return sents, _merge_short(shots, min_shot)


def plan_effects(sents: list[list[Cue]], hook_seconds: float = 3.0) -> dict[int, str]:
    eff: dict[int, str] = {}
    last_slam = -2
    t0 = sents[0][0].start if sents else 0
    for sn, sent in enumerate(sents):
        closing = sn == len(sents) - 1
        for k, c in enumerate(sent):
            eff[c.i] = "pop"
            core = _core(c.text)
            if k == len(sent) - 1:
                # a short punch predicate is a reveal slam; anything longer closes with a fade
                punch = hangul_len(core) <= 5 and core.endswith(("입니다", "니다", "이다")) and len(sent) > 1 and not closing
                eff[c.i] = "slam" if punch else "fade"
            elif k > 0 and _ends(c.text, COORDINATE) and not _ends(c.text, SETUP) and c.text.strip().endswith(","):
                eff[c.i] = "fade"
        if closing or len(sent) < 2 or any(_core(c.text).startswith(CTA) for c in sent):
            continue
        in_hook = (sent[0].start - t0) / TPS < hook_seconds
        if is_question(sent[-1].text) and not in_hook:
            continue
        if any(eff[c.i] == "slam" for c in sent):
            continue
        # the focus phrase: the last non-tail caption, preferring one that ends on a subject/object particle
        body = [c for c in sent[:-1] if eff[c.i] == "pop" and (sent.index(c) > 0 or len(sent) == 2)]
        if not body:
            continue
        marked = [c for c in body if _core(c.text).endswith(("을", "를", "이", "가", "은", "는"))]
        pick = (marked or body)[-1]
        if pick.i - last_slam <= 1:
            continue
        eff[pick.i] = "slam"
        last_slam = pick.i
    return eff


def plan_sounds(sents: list[list[Cue]], shots: list[Shot], eff: dict[int, str], min_gap: float = 0.5) -> dict[int, str]:
    shot_starts = {s.cues[0].i for s in shots}
    sounds: dict[int, str] = {}
    last_whoosh = ""
    rot = 0
    for sn, sent in enumerate(sents):
        closing = sn >= len(sents) - 1
        for k, c in enumerate(sent):
            name = ""
            if c.i == 0:
                name = "W3"
            elif k == 0 and c.i in shot_starts and eff.get(c.i) == "pop":
                if sn == 1 or sn >= len(sents) - 2:
                    name = "W4"
                else:
                    name = WHOOSH_BODY[rot % len(WHOOSH_BODY)]
                    rot += 1
                if name == last_whoosh:
                    name = WHOOSH_BODY[rot % len(WHOOSH_BODY)]
                    rot += 1
                last_whoosh = name
            if closing and k == len(sent) - 1:
                name = "티딩"
            elif eff.get(c.i) == "slam":
                in_hook = (c.start - sents[0][0].start) / TPS < 3.0
                name = "뚜훅" if in_hook else "hit?"
            elif k == len(sent) - 1 and is_question(c.text):
                name = "Bells 2"
            elif k == len(sent) - 1 and eff.get(c.i) == "fade" and any(w in c.text for w in NEGATIVE):
                name = "난감한 물방울"
            if name:
                sounds[c.i] = name
    # at least min_gap apart: keep the earlier sound
    out, last = {}, None
    cues = {c.i: c for s in sents for c in s}
    for i in sorted(sounds):
        if last is not None and (cues[i].start - cues[last].start) / TPS < min_gap:
            continue
        out[i] = sounds[i]
        last = i
    return out


def check(sents: list[list[Cue]], shots: list[Shot], min_shot: float) -> list[str]:
    problems = []
    for n, s in enumerate(shots):
        if s.seconds < min_shot:
            problems.append(f"S{n + 1} is {s.seconds:.2f}s (< {min_shot}s)")
    for a, b in zip(shots, shots[1:]):
        if len(a.cues) == 1 and len(b.cues) == 1:
            problems.append(f"S{shots.index(a) + 1}-S{shots.index(b) + 1}: two one-caption shots in a row")
    starts = {s.cues[0].i for s in shots}
    for sent in sents:
        clauses = 1 + sum(1 for c in sent[:-1] if is_clause_end(c.text) or is_list_item(c.text))
        n_shots = sum(1 for c in sent if c.i in starts)
        if n_shots > clauses:
            problems.append(f"sentence '{sent[0].text}…' has {n_shots} shots for {clauses} clauses")
    return problems


def make_plan(cues: list[Cue], *, reveal: tuple[int, ...] = (), min_shot: float = 1.1) -> Plan:
    cues = sorted(cues, key=lambda c: c.start)
    for n, c in enumerate(cues):
        c.i = n
    sents, shots = plan_shots(cues, reveal=reveal, min_shot=min_shot)
    eff = plan_effects(sents)
    plan = Plan(cues=cues, sentences=sents, shots=shots, effects=eff, sounds=plan_sounds(sents, shots, eff))
    plan.problems = check(sents, shots, min_shot)
    return plan


# ---------------------------------------------------------------- inputs

def parse_srt(text: str) -> list[Cue]:
    cues = []
    pat = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip()):
        lines = block.strip().split("\n")
        for k, line in enumerate(lines):
            m = pat.search(line)
            if m:
                g = [int(x) for x in m.groups()]
                s = (g[0] * 3600 + g[1] * 60 + g[2]) + g[3] / 10 ** len(m.group(4))
                e = (g[4] * 3600 + g[5] * 60 + g[6]) + g[7] / 10 ** len(m.group(8))
                body = " ".join(x.strip() for x in lines[k + 1:] if x.strip())
                if body:
                    cues.append(Cue(len(cues), round(s * TPS), round(e * TPS), body))
                break
    return cues


def track_effect(track: dict) -> Optional[str]:
    name = (track.get("name") or "").lower()
    for eff, words in EFFECT_TRACK_WORDS.items():
        if any(w in name for w in words):
            return eff
    return None


def cues_from_sequence(seq: dict) -> list[Cue]:
    cues = []
    for tid in seq["video_tracks"]:
        t = seq["tracks"][tid]
        if t.get("caption_style") is None:
            continue
        eff = track_effect(t)
        for c in t["clips"].values():
            cap = c.get("caption") or {}
            if cap.get("text", "").strip():
                cues.append(Cue(0, c["start"], c["end"], cap["text"].strip(), eff, c["id"]))
    return cues


# ---------------------------------------------------------------- output

def to_dict(plan: Plan, fps_num: int, fps_den: int) -> dict:
    def f(t: int) -> int:
        return timebase.ticks_to_frames(t, fps_num, fps_den)

    return {
        "shots": [{"n": n + 1, "at": f"{f(s.start)}f", "end": f"{f(s.end)}f", "seconds": round(s.seconds, 2),
                   "captions": [c.i for c in s.cues], "text": s.text, "why": s.reason} for n, s in enumerate(plan.shots)],
        "captions": [{"i": c.i, "frame": f(c.start), "text": c.text, "clip_id": c.clip_id, "now": c.effect,
                      "effect": plan.effects.get(c.i), "sound": plan.sounds.get(c.i)} for c in plan.cues],
        "problems": plan.problems,
    }


def render(plan: Plan, fps_num: int, fps_den: int) -> str:
    """Compact text: one line per caption, shots marked, then the problems."""
    def f(t: int) -> int:
        return timebase.ticks_to_frames(t, fps_num, fps_den)

    starts = {s.cues[0].i: (n + 1, s) for n, s in enumerate(plan.shots)}
    sent_end = {s[-1].i for s in plan.sentences}
    lines = [f"{len(plan.shots)} shots, {len(plan.sentences)} sentences, {len(plan.cues)} captions. "
             "Columns: caption# frame effect(now→plan) sound | text. '┃S' = a new shot (frames, seconds, why); "
             "▪ = sentence end; 'hit?' = choose by meaning: " + " / ".join(HIT_CHOICES) + "."]
    for c in plan.cues:
        if c.i in starts:
            n, s = starts[c.i]
            lines.append(f"┃S{n} {f(s.start)}f-{f(s.end)}f {s.seconds:.2f}s {s.reason}")
        eff = plan.effects.get(c.i, "")
        now = f"{c.effect}→" if c.effect and c.effect != eff else ""
        snd = plan.sounds.get(c.i, "")
        lines.append(f"  {c.i:>3} {f(c.start):>5}f {now}{eff:<5} {snd:<14}| {c.text}{'  ▪' if c.i in sent_end else ''}")
    if plan.problems:
        lines.append("problems: " + "; ".join(plan.problems))
    return "\n".join(lines)
