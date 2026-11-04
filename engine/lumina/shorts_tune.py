"""Tune the shorts_plan draft's rules on the user's archive (no tokens).

Coordinate descent over every knob of shorts_plan.Rules, one group at a time:
cut rules on cut F1, effect rules on effect agreement, sound rules on sound
presence F1 + half the exact-sound rate. Rules are chosen on two thirds of the
Shorts and scored on the other third too, so a rule that only fits the training
Shorts shows as a gap. ``write`` stores the result in lumina/shorts_rules.json,
which shorts_plan loads at start.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, replace
from pathlib import Path

from . import shorts_calibrate as sc
from . import shorts_plan as sp

INF = float("inf")
OPTIONS = {
    "cuts": {
        "clause_age": [0.5, 0.8, 1.0, 1.3, 1.6, 2.0, 2.5, INF],
        "list_age": [0.8, 1.1, 1.5, 2.0, 2.5, INF],
        "long_age": [1.2, 1.5, 1.8, 2.1, 2.5, 3.0, 4.0, INF],
        "long_after_bound": [False, True],
        "tail_protect": [True, False],
        "sentence_hold": [0.4, 0.7, 1.0],
        "lead_in": [0.3, 0.4, 0.5, 0.6],
    },
    "effects": {
        "fade_mid": ["coordinate_comma", "coordinate", "clause", "none"],
        "slam_pick": ["marked_last", "last", "marked_first", "first"],
        "slam_min_caps": [2, 3, 4],
        "slam_questions": ["hook", "always", "never"],
        "punch_len": [0, 5, 7],
    },
    "sounds": {
        "hit_policy": ["all_slams", "hook_only", "none"],
        "whoosh": ["rotate", "w4", "none"],
        "whoosh_on": ["sentence_shot", "every_sentence"],
        "teeding": ["last", "cta", "none"],
        "bells_question": [True, False],
        "letdown": [True, False],
    },
}


def prepare(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        if len(r.get("captions", [])) < 2:
            continue
        fps = r.get("fps") or 30
        tol = 2.5 / fps
        cues = [sp.Cue(n, round(c["start"] * sp.TPS), round(c["end"] * sp.TPS), c["text"], c.get("effect"))
                for n, c in enumerate(r["captions"])]
        starts = sc.user_cut_starts(r)
        cuts = {k for k in range(1, len(cues)) if any(abs(cues[k].start / sp.TPS - s) <= tol for s in starts)}
        out.append({"cues": cues, "cuts": cuts, "sounds": sc.user_sounds(r, cues, tol) if "sounds" in r else None})
    return out


def _f1(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else 1.0
    q = tp / (tp + fn) if tp + fn else 1.0
    return 2 * p * q / (p + q) if p + q else 0.0


def score(data: list[dict], rules: sp.Rules) -> dict:
    tp = fp = fn = 0
    caps = shots_n = 0
    e_ok = e_n = 0
    s_tp = s_fp = s_fn = same = both = 0
    for d in data:
        plan = sp.make_plan(d["cues"], rules=rules)
        mine = {s.cues[0].i for s in plan.shots} - {0}
        tp += len(mine & d["cuts"])
        fp += len(mine - d["cuts"])
        fn += len(d["cuts"] - mine)
        caps += len(plan.cues)
        shots_n += len(plan.shots)
        for c in plan.cues:
            if c.effect in ("pop", "slam", "fade"):
                e_n += 1
                e_ok += plan.effects.get(c.i) == c.effect
        if d["sounds"] is not None:
            for c in plan.cues:
                u, p = d["sounds"].get(c.i), plan.sounds.get(c.i)
                s_tp += bool(u) and bool(p)
                s_fp += bool(p) and not u
                s_fn += bool(u) and not p
                if u and p:
                    both += 1
                    same += u == p or (p == "hit?" and sc.sound_kind(u) == "hit")
    sf1 = _f1(s_tp, s_fp, s_fn)
    return {"cut_f1": round(_f1(tp, fp, fn), 4), "captions_per_shot": round(caps / shots_n, 2) if shots_n else None,
            "effect_acc": round(e_ok / e_n, 4) if e_n else None,
            "sound_f1": round(sf1, 4), "sound_same": round(same / both, 4) if both else None,
            "sound_score": round(sf1 + 0.5 * (same / both if both else 0), 4)}


OBJECTIVE = {"cuts": "cut_f1", "effects": "effect_acc", "sounds": "sound_score"}


MIN_GAIN = 0.002   # a change must buy at least this much on the training Shorts, or the documented rule stays


def tune(rows: list[dict], *, passes: int = 2, min_gain: float = MIN_GAIN, log=print) -> dict:
    data = prepare(rows)
    train = [d for n, d in enumerate(data) if n % 3 != 2]
    test = [d for n, d in enumerate(data) if n % 3 == 2]
    base = sp.Rules()
    rules = replace(base)
    t0 = time.time()
    for group in ("cuts", "effects", "sounds"):
        key = OBJECTIVE[group]
        best = score(train, rules)[key] or 0
        for _ in range(passes):
            changed = False
            for knob, values in OPTIONS[group].items():
                for v in values:
                    if getattr(rules, knob) == v:
                        continue
                    trial = replace(rules, **{knob: v})
                    got = score(train, trial)[key] or 0
                    if got > best + min_gain:
                        before_test = score(test, rules)[key] or 0
                        best, rules, changed = got, trial, True
                        after_test = score(test, rules)[key] or 0
                        log(f"  {group:<7} {knob} = {v}  -> {key} train {got:.4f}, test {before_test:.4f} -> "
                            f"{after_test:.4f}  ({time.time() - t0:.0f}s)")
            if not changed:
                break
    return {"shorts": len(data), "train": len(train), "test": len(test),
            "before": {"train": score(train, base), "test": score(test, base), "all": score(data, base)},
            "after": {"train": score(train, rules), "test": score(test, rules), "all": score(data, rules)},
            "rules": {k: ("inf" if v == INF else v) for k, v in asdict(rules).items()},
            "changed": {k: ("inf" if v == INF else v) for k, v in asdict(rules).items() if getattr(base, k) != v}}


def render(rep: dict) -> str:
    L = [f"{rep['shorts']} Shorts (rules chosen on {rep['train']}, checked on the other {rep['test']})",
         f"{'':<10}{'cut F1':>8}{'cap/shot':>10}{'effects':>9}{'sound F1':>10}{'same snd':>10}"]
    for when in ("before", "after"):
        for part in ("train", "test"):
            m = rep[when][part]
            L.append(f"{when + ' ' + part:<14}{m['cut_f1']:>4.3f}{m['captions_per_shot']:>10}{m['effect_acc'] or 0:>9.3f}"
                     f"{m['sound_f1']:>10.3f}{(m['sound_same'] or 0):>10.3f}")
    L.append("(user's captions per shot in the archive: see calibrate_plan.py)")
    L.append("changed rules: " + (", ".join(f"{k}={v}" for k, v in rep["changed"].items()) or "none"))
    return "\n".join(L)


def write(rep: dict, path=None) -> Path:
    p = Path(path) if path else Path(__file__).with_name("shorts_rules.json")
    p.write_text(json.dumps({"tuned": time.strftime("%Y-%m-%d"), "shorts": rep["shorts"], "rules": rep["rules"],
                             "after_test": rep["after"]["test"], "before_test": rep["before"]["test"]},
                            ensure_ascii=False, indent=1), encoding="utf-8")
    return p
