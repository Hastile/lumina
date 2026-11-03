"""How close the shorts_plan draft is to the user's own Shorts, measured on the archive (no tokens).

Input: the JSON lines that docs/shorts-editing/cut_grammar_extract.py writes
from the Premiere archive (each Short's captions with their effect, and its
picture clips on V1-V3). For every Short the captions go through
shorts_plan, and the draft is compared with what the user did:

  cuts     at each caption boundary, did the user cut (a picture starts within
           2.5 frames of the caption start) and did the draft? precision,
           recall and F1, overall and per kind of boundary (sentence end,
           clause end, list item, bound, lead-in, inside a phrase), so a rule
           that cuts too often or too rarely shows by name
  effects  the draft's pop/slam/fade against the user's, as a confusion table
  shots    captions per shot and shot seconds, draft against user
  sounds   (rows with "sounds", from the A2-A4 tracks) per caption: did both put a
           sound there, the same kind (whoosh / hit / bells / ...) and the same
           sound; and for each sound the draft proposes, what the user had there
  worst    the Shorts the draft gets most wrong, to read by hand

Run it after changing a rule in shorts_plan; keep the change if F1 goes up.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from statistics import mean, median
from typing import Iterable

from . import shorts_plan as sp

TPS = sp.TPS


def load(lines: Iterable[str]) -> list[dict]:
    """Short sequences, deduplicated by their caption text (copies of a project count once)."""
    seen, out = set(), []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        key = tuple(c["text"] for c in r.get("captions", []))
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def boundary_kind(prev: sp.Cue, sentence_end: bool) -> str:
    if sentence_end:
        return "sentence end"
    if prev.seconds < 0.5:
        return "lead-in"
    if sp.is_bound(prev.text):
        return "bound"
    if sp.is_clause_end(prev.text):
        return "clause end"
    if sp.is_list_item(prev.text):
        return "list item"
    return "inside phrase"


def user_cut_starts(row: dict) -> list[float]:
    """Times where the topmost picture of V1-V3 changes: each clip's start where it is the top clip."""
    pics = row.get("pictures", [])
    starts = []
    for p in pics:
        covered = any(q is not p and q["track"] > p["track"] and q["start"] <= p["start"] < q["end"] for q in pics)
        if not covered:
            starts.append(p["start"])
    for q in pics:  # a top clip ending uncovers what is below: that is a change too
        if q["track"] > 0 and any(p["track"] < q["track"] and p["start"] < q["end"] < p["end"] for p in pics):
            starts.append(q["end"])
    return sorted(starts)


PREROLL = {"epic hit": 0.7}
KIND_OF = {"Kick 2": "card", "Bells 2": "bells", "난감한 물방울": "letdown", "티딩": "payoff"}
HITS = ("뚜훅", "H:Cash", "H:Guns", "H:Impactor", "H:Deep", "Kick 1", "Kick 5", "Epic Hit")


def canonical_sound(name: str) -> str:
    """A sound file name as the grammar names it: 'KF - Whoosh Dramatic 3.wav' -> W3, 'Hit - Guns 2' -> H:Guns."""
    import re

    n = re.sub(r"\.(wav|mp3|aif+|m4a|ogg|flac)$", "", name.strip(), flags=re.I)
    low = n.lower()
    m = re.search(r"whoosh[^0-9]*(\d+)", low)
    if m:
        return f"W{m.group(1)}"
    if "whoosh" in low:
        return "W?"
    for pat, fmt in ((r"kick[^0-9]*(\d+)", "Kick {}"), (r"epic\s*hit[^0-9]*(\d+)", "Epic Hit {}"), (r"bells?[^0-9]*(\d+)", "Bells {}")):
        m = re.search(pat, low)
        if m:
            return fmt.format(m.group(1))
    for word, canon in (("guns", "H:Guns"), ("cash", "H:Cash"), ("impactor", "H:Impactor"), ("deep", "H:Deep"),
                        ("뚜훅", "뚜훅"), ("티딩", "티딩"), ("물방울", "난감한 물방울")):
        if word in low:
            return canon
    return n


def sound_kind(canon: str) -> str:
    if canon.startswith("W"):
        return "whoosh"
    if canon == "hit?" or canon.startswith(HITS):
        return "hit"
    return KIND_OF.get(canon, "other")


def user_sounds(row: dict, cues: list, tol: float) -> dict[int, str]:
    """Caption index -> the user's sound landing on its first frame (Epic Hit counted where its hit lands)."""
    out: dict[int, str] = {}
    for snd in row.get("sounds") or []:
        canon = canonical_sound(snd["name"])
        land = snd["start"] + next((v for k, v in PREROLL.items() if k in canon.lower()), 0.0)
        for c in cues:
            if abs(c.start / TPS - land) <= tol and c.i not in out:
                out[c.i] = canon
                break
    return out


def compare(row: dict, rules=None) -> dict:
    fps = row.get("fps") or 30
    tol = 2.5 / fps
    cues = [sp.Cue(n, round(c["start"] * TPS), round(c["end"] * TPS), c["text"], c.get("effect")) for n, c in enumerate(row["captions"])]
    plan = sp.make_plan(cues, rules=rules)
    starts = user_cut_starts(row)
    plan_cut = {s.cues[0].i for s in plan.shots}
    sent_end = {s[-1].i for s in plan.sentences}
    bounds = []
    for k in range(1, len(plan.cues)):
        t = plan.cues[k].start / TPS
        user = any(abs(t - s) <= tol for s in starts)
        bounds.append({"kind": boundary_kind(plan.cues[k - 1], plan.cues[k - 1].i in sent_end), "user": user,
                       "plan": k in plan_cut})
    user_shots = []
    cur = 0
    for k, b in enumerate(bounds, start=1):
        if b["user"]:
            user_shots.append(k - cur)
            cur = k
    user_shots.append(len(plan.cues) - cur)
    effects = [(c.effect, plan.effects[c.i]) for c in plan.cues if c.effect in ("pop", "slam", "fade")]
    sounds = None
    if "sounds" in row:
        theirs = user_sounds(row, plan.cues, tol)
        sounds = [(theirs.get(c.i), plan.sounds.get(c.i)) for c in plan.cues]
    tp = sum(b["user"] and b["plan"] for b in bounds)
    fp = sum(b["plan"] and not b["user"] for b in bounds)
    fn = sum(b["user"] and not b["plan"] for b in bounds)
    return {"project": row.get("project"), "sequence": row.get("sequence"), "bounds": bounds, "effects": effects,
            "plan_shots": [len(s.cues) for s in plan.shots], "plan_seconds": [s.seconds for s in plan.shots],
            "user_shots": user_shots, "f1": _f1(tp, fp, fn), "sounds": sounds}


def _f1(tp: int, fp: int, fn: int) -> float:
    p = tp / (tp + fp) if tp + fp else 1.0
    r = tp / (tp + fn) if tp + fn else 1.0
    return 2 * p * r / (p + r) if p + r else 0.0


def calibrate(rows: list[dict], rules=None) -> dict:
    per = [compare(r, rules) for r in rows if len(r.get("captions", [])) >= 2]
    bounds = [b for x in per for b in x["bounds"]]
    tp = sum(b["user"] and b["plan"] for b in bounds)
    fp = sum(b["plan"] and not b["user"] for b in bounds)
    fn = sum(b["user"] and not b["plan"] for b in bounds)
    by = defaultdict(Counter)
    for b in bounds:
        by[b["kind"]]["n"] += 1
        by[b["kind"]]["user"] += b["user"]
        by[b["kind"]]["plan"] += b["plan"]
        by[b["kind"]]["both"] += b["user"] and b["plan"]
    conf = Counter((u, p) for x in per for u, p in x["effects"])
    pairs = [pr for x in per if x["sounds"] is not None for pr in x["sounds"]]
    sounds = None
    if pairs:
        stp = sum(bool(u) and bool(p) for u, p in pairs)
        sfp = sum(bool(p) and not u for u, p in pairs)
        sfn = sum(bool(u) and not p for u, p in pairs)
        both = [(u, p) for u, p in pairs if u and p]
        by_plan: dict[str, Counter] = defaultdict(Counter)
        for u, p in pairs:
            if p:
                by_plan[p][u or "(nothing)"] += 1
        sounds = {
            "captions": len(pairs), "user_has": sum(bool(u) for u, _ in pairs), "draft_has": sum(bool(p) for _, p in pairs),
            "precision": round(stp / (stp + sfp), 3) if stp + sfp else None,
            "recall": round(stp / (stp + sfn), 3) if stp + sfn else None,
            "same_kind": round(sum(sound_kind(u) == sound_kind(p) for u, p in both) / len(both), 3) if both else None,
            "same_sound": round(sum(u == p for u, p in both) / len(both), 3) if both else None,
            "by_draft_sound": {p: {**dict(c.most_common(5)), "n": sum(c.values())} for p, c in
                               sorted(by_plan.items(), key=lambda kv: -sum(kv[1].values()))},
            "user_kinds": dict(Counter(sound_kind(u) for u, _ in pairs if u).most_common()),
        }
    return {
        "shorts": len(per), "boundaries": len(bounds),
        "cuts": {"precision": round(tp / (tp + fp), 3) if tp + fp else None,
                 "recall": round(tp / (tp + fn), 3) if tp + fn else None,
                 "f1": round(_f1(tp, fp, fn), 3), "agreement": round(sum(b["user"] == b["plan"] for b in bounds) / len(bounds), 3)
                 if bounds else None},
        "by_kind": {k: {"n": v["n"], "user_cut": round(v["user"] / v["n"], 2), "plan_cut": round(v["plan"] / v["n"], 2),
                        "both": v["both"]} for k, v in sorted(by.items(), key=lambda kv: -kv[1]["n"])},
        "effects": {f"{u}->{p}": n for (u, p), n in sorted(conf.items())},
        "effect_agreement": round(sum(n for (u, p), n in conf.items() if u == p) / sum(conf.values()), 3) if conf else None,
        "captions_per_shot": {"user": round(mean(n for x in per for n in x["user_shots"]), 2) if per else None,
                              "plan": round(mean(n for x in per for n in x["plan_shots"]), 2) if per else None},
        "sounds": sounds,
        "plan_shot_seconds_median": round(median(s for x in per for s in x["plan_seconds"]), 2) if per else None,
        "worst": [{"project": x["project"], "sequence": x["sequence"], "f1": round(x["f1"], 2)}
                  for x in sorted(per, key=lambda x: x["f1"])[:10]],
    }


def render(rep: dict) -> str:
    c = rep["cuts"]
    L = [f"{rep['shorts']} Shorts, {rep['boundaries']} caption boundaries",
         f"cuts: precision {c['precision']}  recall {c['recall']}  F1 {c['f1']}  agreement {c['agreement']}",
         "by boundary (user cut rate vs draft cut rate; a big gap = a rule to tune):"]
    for k, v in rep["by_kind"].items():
        flag = "  <-- " + ("draft cuts too often" if v["plan_cut"] - v["user_cut"] > 0.15 else "draft cuts too rarely") \
            if abs(v["plan_cut"] - v["user_cut"]) > 0.15 else ""
        L.append(f"  {k:<14} n={v['n']:<5} user {v['user_cut']:.2f}  draft {v['plan_cut']:.2f}{flag}")
    L.append(f"effects: agreement {rep['effect_agreement']}  " + "  ".join(f"{k} {v}" for k, v in rep["effects"].items()))
    cps = rep["captions_per_shot"]
    L.append(f"captions per shot: user {cps['user']}  draft {cps['plan']}; draft shot median {rep['plan_shot_seconds_median']}s")
    snd = rep.get("sounds")
    if snd:
        L.append(f"sounds: {snd['captions']} captions, user {snd['user_has']} / draft {snd['draft_has']} with a sound; "
                 f"precision {snd['precision']}  recall {snd['recall']}  same kind {snd['same_kind']}  same sound {snd['same_sound']}")
        L.append("  what the user had where the draft put each sound:")
        for p, c in list(snd["by_draft_sound"].items())[:12]:
            top = ", ".join(f"{k} {v}" for k, v in c.items() if k != "n")
            L.append(f"    {p:<14} n={c['n']:<4} {top}")
        L.append("  user's sounds by kind: " + ", ".join(f"{k} {v}" for k, v in snd["user_kinds"].items()))
    else:
        L.append("sounds: no sound data (re-run cut_grammar_extract.py: it now records A2-A4)")
    L.append("worst Shorts (read these by hand):")
    for w in rep["worst"]:
        L.append(f"  F1 {w['f1']:.2f}  {w['sequence']}  ({w['project']})")
    return "\n".join(L)
