"""Read every Shorts sequence in the Premiere archive (read-only) and dump captions + picture cuts as JSON lines."""
import json
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from lumina.io import prproj

TPS = 254_016_000_000


def caption_of(it):
    layers = [l for l in (it.get("graphic") or {}).get("layers", []) if l.get("type") == "text" and (l.get("text") or "").strip()]
    if not layers:
        return None
    main = max(layers, key=lambda l: l.get("font_size") or 0)
    y = (main.get("position") or [0, 0])[1]
    if main.get("box"):
        y += main["box"][1] / 2
    if not 900 <= y <= 1250:
        return None
    eff = "none"
    for fx in it.get("effects") or []:
        if fx.get("type") != "transform":
            continue
        kf = fx.get("keyframes") or {}
        sc = kf.get("scale") or []
        op = kf.get("opacity") or []
        if op and op[0]["v"] < 50:
            eff = "fade"
        elif sc and sc[0]["v"] >= 130:
            eff = "slam"
        elif sc and sc[0]["v"] < 100:
            eff = "pop"
        elif kf.get("position"):
            eff = "fade"
    return {"start": it["start"] / TPS, "end": it["end"] / TPS, "text": main["text"].replace("\n", " ").strip(),
            "size": main.get("font_size"), "effect": eff}


def one(path):
    out = []
    try:
        ex = prproj.extract(path)
    except Exception as exc:  # noqa: BLE001
        return path, [], repr(exc)[:200]
    for s in ex.sequences:
        vt = s["video_tracks"]
        names = [it["name"] for t in vt for it in t["items"]]
        if not any("쇼츠 타이틀" in n for n in names):
            continue
        caps = []
        for ti, t in enumerate(vt):
            if ti < 3:
                continue
            for it in t["items"]:
                if it["name"] == "쇼츠 타이틀" or not it.get("graphic"):
                    continue
                c = caption_of(it)
                if c and c["end"] - c["start"] < 8:
                    caps.append(c)
        caps.sort(key=lambda c: c["start"])
        # picture: media clips on V1-V3 (topmost wins), plus graphics/stills on V1
        pics = []
        for ti, t in enumerate(vt[:3]):
            for it in t["items"]:
                if it.get("media_uid") or it.get("sequence_uid") or it.get("adjustment"):
                    if it.get("adjustment"):
                        continue
                    m = it.get("motion") or {}
                    pics.append({"track": ti, "start": it["start"] / TPS, "end": it["end"] / TPS, "name": it["name"],
                                 "src": (it.get("source_in") or 0) / TPS, "speed": it.get("speed", 1.0), "scale": m.get("scale")})
        if len(caps) < 8 or not pics:
            continue
        # sound effects on A2-A4 (A1 is the narration, A5 the BGM), for the SFX half of calibrate_plan.py
        sounds = [{"track": ti, "start": it["start"] / TPS, "end": it["end"] / TPS, "name": it["name"]}
                  for ti, t in enumerate(s.get("audio_tracks") or []) if 1 <= ti <= 3 for it in t["items"]]
        out.append({"project": path, "sequence": s["name"], "fps": s["settings"]["fps_num"] / s["settings"]["fps_den"],
                    "captions": caps, "pictures": pics, "sounds": sorted(sounds, key=lambda x: x["start"])})
    return path, out, None


if __name__ == "__main__":
    root = Path(sys.argv[1])
    dest = Path(sys.argv[2])
    files = [str(p) for p in root.rglob("*.prproj") if "Auto-Save" not in str(p)]
    n = 0
    with ProcessPoolExecutor(max_workers=8) as pool, dest.open("w", encoding="utf-8") as f:
        futs = [pool.submit(one, p) for p in files]
        for fu in as_completed(futs):
            path, seqs, err = fu.result()
            if err:
                print("ERR", path, err, file=sys.stderr)
            for s in seqs:
                f.write(json.dumps(s, ensure_ascii=False) + "\n")
                n += 1
    print("sequences", n, "files", len(files))
