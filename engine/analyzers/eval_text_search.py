"""How well SigLIP finds the right cut from text, measured on the source DB.

    uv run --project engine/analyzers python engine/analyzers/eval_text_search.py captions [n]
    uv run --project engine/analyzers python engine/analyzers/eval_text_search.py descriptions [n]

captions:      query = a narration caption from a past edit (usage), target = the cut
               the editor put under it.
descriptions:  query = a Claude description of a cut, target = that cut (an upper bound:
               the description was written from that very keyframe).
The pool is every embedded cut of the target's work. Reports recall@8/20/50 next
to the random baseline.

Measured 2026-11-03 (55k cuts): captions 0.07 / 0.12 / 0.19 (random 0.02 / 0.05 / 0.10,
n=596, median pool 2317); descriptions 0.77 / 0.89 / 0.96 (random 0.03 / 0.07 / 0.16,
n=395, median pool 536). After the 엣지러너 2 folder was re-cut and described, the
pools grew: descriptions 0.58 / 0.66 / 0.78 (n=120, median pool 4816). Hence
source_plan asks for a picture description (look); larger pools need it more.
"""

from __future__ import annotations

import collections
import random
import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE))

import numpy as np  # noqa: E402

from lumina.sourcedb import db  # noqa: E402
from text_encoder import MAX_TOKENS, MODELS, NAME  # noqa: E402

KS = (8, 20, 50)


def queries(con, mode: str, by_media, work_of) -> list[dict]:
    if mode == "captions":
        rows = con.execute("SELECT media_id, src_in_ticks AS t0, src_out_ticks AS t1, caption AS q FROM usage "
                           "WHERE caption IS NOT NULL").fetchall()
        rows = [dict(r) for r in rows if r["q"] and len(r["q"].strip()) >= 4]
    else:
        rows = [dict(r) for r in con.execute(
            "SELECT media_id, t0_ticks AS t0, t1_ticks AS t1, value AS q FROM fact "
            "WHERE kind = 'description' AND producer LIKE 'claude%'")]
    return [r for r in rows if r["media_id"] in by_media and work_of.get(r["media_id"])]


def main() -> None:
    import torch
    from transformers import AutoModel, AutoProcessor

    mode = sys.argv[1] if len(sys.argv) > 1 else "captions"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 400
    con = db.connect(db.DEFAULT_DB_PATH)
    keys, vecs = db.load_embeddings(con, "siglip2-so400m-384")
    vecs /= np.maximum(np.linalg.norm(vecs, axis=1, keepdims=True), 1e-6)
    key_idx = {(int(a), int(b)): i for i, (a, b) in enumerate(keys)}
    by_media = collections.defaultdict(list)
    for c in con.execute("SELECT cut_id, media_id, start_ticks, end_ticks, keyframe_ticks FROM cut"):
        i = key_idx.get((c["media_id"], c["keyframe_ticks"]))
        if i is not None:
            by_media[c["media_id"]].append((dict(c), i))
    work_of = collections.defaultdict(set)
    for r in con.execute("SELECT media_id, ref_id FROM fact WHERE kind = 'work' AND status != 'rejected' "
                         "AND ref_id IS NOT NULL"):
        work_of[r[0]].add(r[1])
    media_of_work = collections.defaultdict(set)
    for m, ws in work_of.items():
        for w in ws:
            media_of_work[w].add(m)
    kind = dict(con.execute("SELECT media_id, kind FROM media").fetchall())

    rows = queries(con, mode, by_media, work_of)
    random.seed(1)
    random.shuffle(rows)
    rows = rows[:n]

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = AutoModel.from_pretrained(NAME, cache_dir=MODELS, dtype=torch.float16 if device == "cuda" else torch.float32)
    model = model.to(device).eval()
    proc = AutoProcessor.from_pretrained(NAME, cache_dir=MODELS)
    texts = [r["q"] for r in rows]
    enc = []
    for i in range(0, len(texts), 64):
        x = proc(text=texts[i:i + 64], padding="max_length", max_length=MAX_TOKENS, truncation=True,
                 return_tensors="pt").to(device)
        with torch.no_grad():
            f = model.get_text_features(**x)
            f = getattr(f, "pooler_output", f)
            enc.append(torch.nn.functional.normalize(f.float(), dim=-1).cpu().numpy())
    T = np.concatenate(enc)

    hits, done, pools = collections.Counter(), 0, []
    for r, t in zip(rows, T):
        pool = [ci for m in set().union(*(media_of_work[w] for w in work_of[r["media_id"]]))
                for ci in by_media.get(m, [])]
        t0, t1 = (r["t0"], r["t1"]) if r["t0"] is not None else (0, 1)
        target = {c["cut_id"] for c, _ in by_media[r["media_id"]]
                  if kind[r["media_id"]] == "image" or (c["start_ticks"] < t1 and c["end_ticks"] > t0)}
        if not target or len(pool) < 20:
            continue
        order = np.argsort(-(vecs[[i for _, i in pool]] @ t))
        rank = next((k for k, j in enumerate(order) if pool[j][0]["cut_id"] in target), 10 ** 9)
        done += 1
        pools.append(len(pool))
        for k in KS:
            hits[k] += rank < k
            hits[f"r{k}"] += min(1, len(target) * k / len(pool))
    print(f"{mode}: n={done}, median pool {int(np.median(pools))} cuts")
    for k in KS:
        print(f"  recall@{k}: siglip {hits[k] / done:.2f}  random {hits[f'r{k}'] / done:.2f}")


if __name__ == "__main__":
    main()
