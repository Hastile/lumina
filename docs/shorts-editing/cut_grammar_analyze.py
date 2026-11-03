import json
import sys
from collections import Counter, defaultdict

rows = [json.loads(l) for l in open(sys.argv[1], encoding="utf-8")]
seen, seqs = set(), []
for r in rows:
    key = tuple(c["text"] for c in r["captions"])
    if key in seen:
        continue
    seen.add(key)
    seqs.append(r)

SENT = ("다", "요", "죠", "까", "?", "!", ".", "네", "군", "걸", "니")
CLAUSE = ("고", "며", "서", "면", "는데", "은데", "지만", "니까", "더니", "도록", "려고", "듯", "면서", "게", "때문에", "는지", "든", "니다만", "인데", "던데", "다가", "자")


def ending(t):
    t = t.strip().rstrip("~…. ").rstrip()
    if not t:
        return "phrase"
    if t[-1] in "?!":
        return "sentence"
    if t.endswith(CLAUSE):
        return "clause"
    if t.endswith(SENT):
        return "sentence"
    return "phrase"


B = []  # boundary records
shots = []
for s in seqs:
    fr = 1 / s["fps"]
    tol = 2.5 * fr
    pics = sorted(s["pictures"], key=lambda p: p["start"])
    caps = s["captions"]
    end_t = max(c["end"] for c in caps)

    def top(t):
        best = None
        for p in s["pictures"]:
            if p["start"] <= t < p["end"] and (best is None or p["track"] > best["track"]):
                best = p
        return best

    pts = sorted({round(p["start"], 4) for p in pics} | {round(p["end"], 4) for p in pics})
    changes = []
    prev = top(0.001)
    for t in pts:
        if t <= 0 or t >= end_t:
            continue
        cur = top(t + 0.001)
        if cur is not prev:
            changes.append(t)
        prev = cur
    starts = [c["start"] for c in caps]
    on_cap = [t for t in changes if any(abs(t - c) <= tol for c in starts)]
    shots.append({"n_changes": len(changes), "on_cap": len(on_cap), "dur": end_t, "n_caps": len(caps)})
    last_cut, ncap = 0.0, 0
    for i in range(1, len(caps)):
        a, b = caps[i - 1], caps[i]
        ncap += 1
        t = b["start"]
        cut = any(abs(t - c) <= tol for c in changes)
        # a cut inside the previous caption resets the count
        mids = [c for c in changes if last_cut + tol < c < t - tol]
        if mids:
            last_cut = max(mids)
            ncap = 1
        B.append({
            "prev_eff": a["effect"], "next_eff": b["effect"], "end": ending(a["text"]),
            "gap": b["start"] - a["end"], "age": t - last_cut, "ncap": ncap,
            "prev_dur": a["end"] - a["start"], "next_dur": b["end"] - b["start"],
            "next_len": len(b["text"].replace(" ", "")), "prev_text": a["text"], "next_text": b["text"],
            "cut": cut, "pos": t / end_t,
        })
        if cut:
            last_cut, ncap = t, 0

print("unique shorts", len(seqs), "boundaries", len(B))
tc = sum(s["n_changes"] for s in shots); oc = sum(s["on_cap"] for s in shots)
print(f"picture changes {tc}, on a caption start {oc} ({oc/tc:.0%})")
print(f"overall P(cut at caption boundary) {sum(b['cut'] for b in B)/len(B):.0%}")


def table(name, key, order=None):
    g = defaultdict(list)
    for b in B:
        g[key(b)].append(b["cut"])
    print(f"\n== {name}")
    ks = order or sorted(g, key=lambda k: (str(type(k)), k))
    for k in ks:
        if k in g:
            v = g[k]
            print(f"  {str(k):28s} n={len(v):5d}  cut {sum(v)/len(v):5.0%}")


table("prev caption effect", lambda b: b["prev_eff"])
table("next caption effect", lambda b: b["next_eff"])
table("prev ending", lambda b: b["end"])
table("prev effect x ending", lambda b: (b["prev_eff"], b["end"]))
bucket = lambda x, edges: next((f"<{e}" for e in edges if x < e), f">={edges[-1]}")
table("pause before next caption (s)", lambda b: bucket(b["gap"], [0.05, 0.15, 0.3, 0.6]), ["<0.05", "<0.15", "<0.3", "<0.6", ">=0.6"])
table("shot age at boundary (s)", lambda b: bucket(b["age"], [0.6, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0]), ["<0.6", "<1.0", "<1.5", "<2.0", "<2.5", "<3.0", "<4.0", ">=4.0"])
table("captions already on this shot", lambda b: min(b["ncap"], 6))
table("next caption duration (s)", lambda b: bucket(b["next_dur"], [0.4, 0.6, 0.9, 1.3]), ["<0.4", "<0.6", "<0.9", "<1.3", ">=1.3"])
table("next caption chars", lambda b: bucket(b["next_len"], [3, 5, 8]), ["<3", "<5", "<8", ">=8"])

# combined rule view: ending x shot age
table("ending x age", lambda b: (b["end"], bucket(b["age"], [1.0, 2.0, 3.0])), [(e, a) for e in ("sentence", "clause", "phrase") for a in ("<1.0", "<2.0", "<3.0", ">=3.0")])
table("fade-prev x age", lambda b: (b["prev_eff"] == "fade", bucket(b["age"], [1.0, 2.0, 3.0])), [(f, a) for f in (True, False) for a in ("<1.0", "<2.0", "<3.0", ">=3.0")])

# shot lengths and captions per shot from cut boundaries
caps_per = Counter()
for s in seqs:
    pass
lens = []
for s in seqs:
    caps = s["captions"]
json.dump(B, open(sys.argv[2], "w", encoding="utf-8"), ensure_ascii=False)
